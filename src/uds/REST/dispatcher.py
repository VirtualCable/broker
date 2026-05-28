# -*- coding: utf-8 -*-
#
# Copyright (c) 2012-2024 Virtual Cable S.L.
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without modification,
# are permitted provided that the following conditions are met:
#
#    * Redistributions of source code must retain the above copyright notice,
#      this list of conditions and the following disclaimer.
#    * Redistributions in binary form must reproduce the above copyright notice,
#      this list of conditions and the following disclaimer in the documentation
#      and/or other materials provided with the distribution.
#    * Neither the name of Virtual Cable S.L. nor the names of its contributors
#      may be used to endorse or promote products derived from this software
#      without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
# FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
# SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
# OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

"""
Author: Adolfo Gómez, dkmaster at dkmon dot com
"""
import dataclasses
import logging
import sys
import typing
import collections.abc
import traceback
import json

from django import http
from django.conf import settings
from django.utils.decorators import method_decorator
from django.core.exceptions import ObjectDoesNotExist
from django.views.decorators.csrf import csrf_exempt
from django.views.generic.base import View

from uds.core import consts, exceptions, types
from uds.core.util import modfinder
from uds.core.util.model import sql_stamp_seconds

from . import processors, log
from .handlers import Handler, ErrorHandler
from . import model as rest_model

# Not imported at runtime, just for type checking
if typing.TYPE_CHECKING:
    from uds.core.types.requests import ExtendedHttpRequestWithUser

logger = logging.getLogger(__name__)

__all__ = ['Handler', 'Dispatcher']

T = typing.TypeVar('T', bound=http.HttpResponse)


class Dispatcher(View):
    """
    This class is responsible of dispatching REST requests
    """

    # This attribute will contain all paths--> handler relations, filled at Initialized method
    root_node: typing.ClassVar[types.rest.HandlerNode] = types.rest.HandlerNode('', None, None, {})

    @staticmethod
    def error_response(err_cls: type[T], handler: Handler | None, msg: str, exc: Exception | None = None) -> T:

        # If debug, log the error with traceback
        if getattr(settings, 'DEBUG', False):
            trace_back = traceback.format_exc()
            logger.error('Exception processing request: %s', handler.full_path if handler else 'unknown')
            for i in trace_back.splitlines():
                logger.error('* %s', i)
            # Append error exception to message response
            msg = f'{msg}: {str(exc)}' if exc else msg

        if handler:
            log.log_operation(handler, err_cls.status_code, types.log.LogLevel.ERROR)
        return err_cls(json.dumps({"error": msg}).encode(), content_type="application/json")

    @method_decorator(csrf_exempt)
    def dispatch(self, request: 'http.request.HttpRequest', path: str) -> 'http.HttpResponse':
        """
        Processes the REST request and routes it wherever it needs to be routed
        """
        request = typing.cast('ExtendedHttpRequestWithUser', request)  # Reconverting to typed request
        if not hasattr(request, 'user'):
            raise exceptions.rest.HandlerError('Request does not have a user, cannot process request')

        # Remove session from request, so response middleware do nothing with this
        del request.session

        # Now we extract method and possible variables from path
        # path: list[str] = kwargs['arguments'].split('/')
        # path = kwargs['arguments']
        # del kwargs['arguments']

        # # Transverse service nodes, so we can locate class processing this path
        # service = Dispatcher.services
        # full_path_lst: list[str] = []
        # # Guess content type from content type header (post) or ".xxx" to method
        content_type: str = request.META.get('CONTENT_TYPE', 'application/json').split(';')[0]

        handler_node = Dispatcher.root_node.find_path(path)
        if not handler_node:
            return http.HttpResponseNotFound('Service not found', content_type="text/plain")

        logger.debug("REST request: %s (%s)", handler_node, handler_node.full_path())

        # Now, service points to the class that will process the request
        # We get the '' node, that is the "current" node, and get the class from it
        cls: type[Handler] | None = handler_node.handler
        if not cls:
            return Dispatcher.error_response(
                http.HttpResponseNotFound,
                ErrorHandler(request, handler_node.full_path(), 'get', {}),
                'Method not found',
            )

        processor = processors.available_processors_mime_dict.get(content_type, processors.default_processor)(
            request
        )

        # Obtain method to be invoked
        http_method: str = request.method.lower() if request.method else ''
        # ensure method is recognized
        if http_method not in ('get', 'post', 'put', 'delete'):
            return Dispatcher.error_response(
                http.HttpResponseNotAllowed,
                ErrorHandler(request, handler_node.full_path(), http_method, {}),
                f'Method {http_method.upper()} not allowed',
            )

        node_full_path: typing.Final[str] = handler_node.full_path()

        # Path here has "remaining" path, that is, method part has been removed
        args = path[len(node_full_path) :].split('/')[1:]  # First element is always empty, so we skip it

        handler: Handler | None = None

        try:
            handler = cls(
                request,
                node_full_path,
                http_method,
                processor.process_parameters(),
                *args,
            )
            processor.set_odata(handler.odata)
            operation: collections.abc.Callable[[], typing.Any] = getattr(handler, http_method)
        except processors.ParametersException as e:
            return Dispatcher.error_response(http.HttpResponseBadRequest, handler, 'Invalid parameters', e)

        except ValueError as e:
            return Dispatcher.error_response(http.HttpResponseBadRequest, handler, 'Invalid value', e)

        except AttributeError:
            # Special case, allowed methods must be on response, so not using Dispatcher.error_response
            allowed_methods: list[str] = [n for n in ['get', 'post', 'put', 'delete'] if hasattr(handler, n)]
            log.log_operation(handler, 405, types.log.LogLevel.ERROR)
            return http.HttpResponseNotAllowed(
                allowed_methods, content=b'{"error": "Invalid method"}', content_type="application/json"
            )
        except exceptions.rest.AccessDenied:
            return Dispatcher.error_response(http.HttpResponseForbidden, handler, 'Access denied')
        except Exception:
            return Dispatcher.error_response(http.HttpResponseServerError, handler, 'Unexpected error')

        # Invokes the handler's operation, add headers to response and returns
        try:
            response = operation()

            # If response is an HttpResponse object, return it directly
            if not isinstance(response, http.HttpResponse):
                # If it is a generator, produce an streamed incremental response
                if isinstance(response, collections.abc.Generator):
                    response = typing.cast(
                        'http.HttpResponse',
                        http.StreamingHttpResponse(
                            processor.as_incremental(response),
                            content_type="application/json",
                        ),
                    )
                else:
                    response = processor.get_response(response)
            # Set response headers
            response['UDS-Version'] = f'{consts.system.VERSION};{consts.system.VERSION_STAMP}'
            response['Response-Stamp'] = sql_stamp_seconds()

            # Security headers for REST API
            response['Cache-Control'] = 'no-cache, no-store, must-revalidate'
            response['Pragma'] = 'no-cache'
            response['Expires'] = '0'

            for k, val in handler.headers().items():
                response[k] = val

            # Log de operation on the audit log for admin
            # Exceptiol will also be logged, but with ERROR level
            log.log_operation(handler, response.status_code, types.log.LogLevel.INFO)
            return response
            # Note that the order of exceptions is important
            # because some exceptions are subclasses of others
        except exceptions.rest.NotSupportedError as e:
            return Dispatcher.error_response(http.HttpResponseBadRequest, handler, 'Not supported', e)
        except exceptions.rest.AccessDenied as e:
            return Dispatcher.error_response(http.HttpResponseForbidden, handler, 'Access denied', e)
        except exceptions.rest.NotFound as e:
            return Dispatcher.error_response(http.HttpResponseNotFound, handler, 'Not found', e)
        except exceptions.rest.RequestError as e:
            # Request Error has an error message implicit
            return Dispatcher.error_response(http.HttpResponseBadRequest, handler, f'Request error: {e}')
        except exceptions.rest.ResponseError as e:
            # Response Error has an error message implicit
            return Dispatcher.error_response(http.HttpResponseServerError, handler, f'Response error: {e}')
        except exceptions.rest.HandlerError as e:
            return Dispatcher.error_response(http.HttpResponseBadRequest, handler, 'Handler error', e)
        except exceptions.services.generics.Error as e:
            return Dispatcher.error_response(http.HttpResponseServerError, handler, 'Service error', e)
        except ObjectDoesNotExist as e:  # All DoesNotExist exceptions are not found
            return Dispatcher.error_response(http.HttpResponseNotFound, handler, 'Not found', e)
        except ValueError as e:  #  I.e. invalid UUID, invalid parameters, etc
            return Dispatcher.error_response(http.HttpResponseBadRequest, handler, 'Invalid value', e)
        except Exception as e:
            return Dispatcher.error_response(http.HttpResponseServerError, handler, 'Unexpected error', e)

    @staticmethod
    def register_handler(type_: type[Handler]) -> None:
        """
        Method to register a class as a REST service
        param type_: Class to be registered
        """
        if not type_.NAME:
            name = sys.intern(type_.__name__.lower())
        else:
            name = type_.NAME

        # Fill the service_node tree with the class
        service_node = Dispatcher.root_node  # Root path
        # If path, ensure that the path exists on the tree
        if type_.PATH:
            logger.info('Path: /%s/%s', type_.PATH, name)
            for k in type_.PATH.split('/'):
                intern_k = sys.intern(k)
                if intern_k not in service_node.children:
                    service_node.children[intern_k] = types.rest.HandlerNode(k, None, service_node, {})
                service_node = service_node.children[intern_k]
        else:
            logger.info('Path: /%s', name)

        if name not in service_node.children:
            service_node.children[name] = types.rest.HandlerNode(name, None, service_node, {})

        service_node.children[name] = dataclasses.replace(service_node.children[name], handler=type_)

    # Initializes the dispatchers
    @staticmethod
    def initialize() -> None:
        """
        This imports all packages that are descendant of this package, and, after that,
        it register all subclases of Handler. (In fact, it looks for packages inside "methods" package, child of this)
        """
        logger.info('Initializing REST Handlers')
        # Our parent module "REST", because we are in "dispatcher"
        module_name = __name__[: __name__.rfind('.')]

        def checker(x: type[Handler]) -> bool:
            return not issubclass(x, rest_model.DetailHandler) and not x.__subclasses__()

        # Register all subclasses of Handler
        modfinder.dynamically_load_and_register_packages(
            Dispatcher.register_handler,
            Handler,
            module_name=module_name,
            checker=checker,
            package_name='methods',
        )

        logger.info('REST Handlers initialized')


Dispatcher.initialize()
