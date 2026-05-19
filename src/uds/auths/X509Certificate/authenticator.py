#
# Copyright (c) 2024 Virtual Cable S.L.
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
Author: Adolfo Gomez, dkmaster at dkmon dot com
"""
import logging
import typing

from django.urls import reverse
from django.utils.translation import gettext
from django.utils.translation import gettext_noop as _

from uds.core import auths, exceptions, types
from uds.core.ui import gui
from uds.core.util import auth as auth_utils, fields

try:
    from cryptography import x509
    from cryptography.x509 import oid
    from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
    import cryptography.exceptions
except ImportError:
    raise exceptions.ui.ValidationError(
        gettext('Cryptography module is required for X509Certificate authenticator')
    ) from None

if typing.TYPE_CHECKING:
    from uds.core.types.requests import ExtendedHttpRequest

logger = logging.getLogger(__name__)

# Map OID short names to human-readable names used in DN
_OID_TO_SHORT: dict[x509.ObjectIdentifier, str] = {
    oid.NameOID.COMMON_NAME: 'CN',
    oid.NameOID.ORGANIZATION_NAME: 'O',
    oid.NameOID.ORGANIZATIONAL_UNIT_NAME: 'OU',
    oid.NameOID.COUNTRY_NAME: 'C',
    oid.NameOID.STATE_OR_PROVINCE_NAME: 'ST',
    oid.NameOID.LOCALITY_NAME: 'L',
    oid.NameOID.SERIAL_NUMBER: 'SERIALNUMBER',
    oid.NameOID.EMAIL_ADDRESS: 'E',
    oid.NameOID.SURNAME: 'SN',
    oid.NameOID.GIVEN_NAME: 'GN',
    oid.NameOID.TITLE: 'T',
    oid.NameOID.DOMAIN_COMPONENT: 'DC',
    oid.NameOID.USER_ID: 'UID',
    oid.NameOID.JURISDICTION_COUNTRY_NAME: 'JURISDICTIONC',
    oid.NameOID.JURISDICTION_STATE_OR_PROVINCE_NAME: 'JURISDICTIONST',
    oid.NameOID.JURISDICTION_LOCALITY_NAME: 'JURISDICTIONL',
    oid.NameOID.BUSINESS_CATEGORY: 'BUSINESSCATEGORY',
    oid.NameOID.POSTAL_CODE: 'POSTALCODE',
    oid.NameOID.STREET_ADDRESS: 'STREET',
    oid.NameOID.PSEUDONYM: 'PSEUDONYM',
}


def _subject_to_mapping(subject: x509.Name) -> dict[str, list[str]]:
    """Convert x509 Name to a mapping suitable for process_regex_field."""
    result: dict[str, list[str]] = {}
    for attr in subject:
        short = _OID_TO_SHORT.get(attr.oid, attr.oid.dotted_string)
        val = attr.value.strip()
        if short not in result:
            result[short] = []
        result[short].append(val)
    return result


def _verify_cert_signed_by_ca(cert: x509.Certificate, ca_cert: x509.Certificate) -> bool:
    """Verify that the certificate was signed by the CA certificate."""
    if cert.issuer != ca_cert.subject:
        return False

    try:
        ca_public_key = ca_cert.public_key()
        tbs = cert.tbs_certificate_bytes
        sig = cert.signature
        hash_algo = cert.signature_hash_algorithm

        if isinstance(ca_public_key, rsa.RSAPublicKey):
            ca_public_key.verify(sig, tbs, padding.PKCS1v15(), hash_algo)
        elif isinstance(ca_public_key, ec.EllipticCurvePublicKey):
            ca_public_key.verify(sig, tbs, ec.ECDSA(hash_algo))
        else:
            logger.error('Unsupported CA key type: %s', type(ca_public_key).__name__)
            return False
        return True
    except cryptography.exceptions.InvalidSignature:
        return False


class X509CertificateAuthenticator(auths.Authenticator):
    """
    Authenticator that validates users via X509 client certificates.

    Nginx requests the client certificate and forwards it to UDS.
    The authenticator validates the certificate against a trusted CA,
    extracts the username from the subject DN, and authenticates the user.
    """

    type_name = _('X509 Certificate Authenticator')
    type_type = 'X509CertificateAuthenticator'
    type_description = _('X509 Client Certificate Authenticator')
    icon_file = 'auth.png'
    auth_type_group: typing.ClassVar[types.auth.AuthTypeGroup] = types.auth.AuthTypeGroup.CERTIFICATE

    # ---- Certificate fields ----
    ca_certificate = gui.TextField(
        length=16384,
        lines=5,
        label=_('CA Certificate'),
        tooltip=_('PEM-encoded Certificate Authority certificate used to validate client certificates.'),
        required=True,
        tab=_('Certificates'),
    )
    trusted_issuer = gui.TextField(
        length=512,
        label=_('Trusted Issuer'),
        order=10,
        tooltip=_(
            'Expected Issuer DN of client certificates. '
            'Leave empty to accept any certificate signed by the configured CA. '
            'The comparison strips spaces and is case-sensitive.'
        ),
        required=False,
        tab=_('Certificates'),
    )

    # ---- Attribute extraction ----
    username_attr = fields.username_attr_field(tab=_('Attributes'))
    realname_attr = fields.realname_attr_field(tab=_('Attributes'))

    # ---- Groups ----
    common_groups = gui.TextField(
        length=256,
        label=_('Common Groups'),
        order=40,
        tooltip=_('Comma-separated list of groups the user will be assigned to.'),
        required=False,
        tab=_('Groups'),
    )

    # ---- Revocation (future) ----
    ocsp_url = gui.TextField(
        length=256,
        label=_('OCSP URL'),
        order=50,
        tooltip=_('OCSP responder URL for certificate revocation checking (not yet implemented).'),
        required=False,
        tab=_('Revocation'),
    )
    crl_url = gui.TextField(
        length=256,
        label=_('CRL URL'),
        order=51,
        tooltip=_('CRL distribution point URL for certificate revocation checking (not yet implemented).'),
        required=False,
        tab=_('Revocation'),
    )

    def initialize(self, values: dict[str, typing.Any] | None) -> None:
        if not values:
            return

        if not self.ca_certificate.value.strip():
            raise exceptions.ui.ValidationError(
                gettext('CA Certificate is required')
            )

        try:
            ca_cert = x509.load_pem_x509_certificate(self.ca_certificate.value.encode())
            bc = ca_cert.extensions.get_extension_for_class(x509.BasicConstraints)
            if not bc.value.ca:
                raise exceptions.ui.ValidationError(
                    gettext('The provided certificate is not a CA certificate (missing CA:TRUE in Basic Constraints)')
                )
        except x509.ExtensionNotFound:
            raise exceptions.ui.ValidationError(
                gettext('The provided certificate does not have Basic Constraints extension')
            ) from None
        except ValueError as e:
            raise exceptions.ui.ValidationError(
                gettext('Invalid PEM-encoded certificate: {}').format(e)
            ) from e

        auth_utils.validate_regex_field(self.username_attr)
        auth_utils.validate_regex_field(self.realname_attr)

    def auth_callback(
        self,
        parameters: 'types.auth.AuthCallbackParams',
        groups_manager: 'auths.GroupsManager',
        request: 'ExtendedHttpRequest',
    ) -> types.auth.AuthenticationResult:
        cert_bytes = parameters.binary_params
        if not cert_bytes:
            logger.error('No certificate data in callback parameters')
            return types.auth.FAILED_AUTH

        try:
            client_cert = x509.load_pem_x509_certificate(cert_bytes)
            ca_cert = x509.load_pem_x509_certificate(self.ca_certificate.value.encode())

            if not _verify_cert_signed_by_ca(client_cert, ca_cert):
                logger.warning('Certificate not signed by configured CA')
                return types.auth.FAILED_AUTH

            if self.trusted_issuer.value.strip():
                issuer_normalized = client_cert.issuer.rfc4514_string().replace(' ', '')
                configured_normalized = self.trusted_issuer.value.replace(' ', '')
                if issuer_normalized != configured_normalized:
                    logger.warning(
                        'Certificate issuer mismatch: expected %s, got %s',
                        self.trusted_issuer.value,
                        client_cert.issuer.rfc4514_string(),
                    )
                    return types.auth.FAILED_AUTH

            subject_mapping = _subject_to_mapping(client_cert.subject)
            username = ''.join(auth_utils.process_regex_field(self.username_attr.value, subject_mapping))
            if not username:
                logger.warning('Could not extract username from certificate subject')
                return types.auth.FAILED_AUTH

            username = username.replace(' ', '_')

            realname = ' '.join(auth_utils.process_regex_field(self.realname_attr.value, subject_mapping))
            if not realname:
                realname = username.capitalize()  # type: ignore

            groups = [g.strip() for g in self.common_groups.value.split(',') if g.strip()]
            groups_manager.validate(groups)

            self.storage.save_pickled(username, [realname])

            return types.auth.AuthenticationResult(types.auth.AuthenticationState.SUCCESS, username=username)
        except Exception as e:
            logger.error('Error validating certificate: %s', e)
            return types.auth.FAILED_AUTH

    def get_javascript(self, request: 'ExtendedHttpRequest') -> str | None:
        url = reverse('page.auth.cert', kwargs={'auth_uuid': self.db_obj().uuid})
        return f'window.location="{url}";'

    def get_groups(self, username: str, groups_manager: 'auths.GroupsManager') -> None:
        groups_manager.validate(
            [g.strip() for g in self.common_groups.value.split(',') if g.strip()]
        )

    def get_real_name(self, username: str) -> str:
        data = self.storage.read_pickled(username)
        if not data:
            return username
        return data[0]
