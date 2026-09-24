# Flow payloads and CAS snapshots may hold secret field values: store them
# encrypted (whole JSON document as one string, CryptoManager.encrypt_password)
# instead of in clear, and encrypt the rows written before this change.

import typing

from django.db import migrations, models

import uds.models.action_flow
from uds.models.action_flow import decrypt_json, encrypt_json

if typing.TYPE_CHECKING:
    from django.apps.registry import Apps
    from django.db.backends.base.schema import BaseDatabaseSchemaEditor

ENCRYPTED_PROPERTIES: typing.Final[tuple[str, ...]] = ("base_values", "approved_values", "snap_info")


def encrypt_existing(apps: "Apps", schema_editor: "BaseDatabaseSchemaEditor") -> None:
    FlowAction = apps.get_model("uds", "FlowAction")
    Properties = apps.get_model("uds", "Properties")
    for action in FlowAction.objects.exclude(values__isnull=True):
        action.save(update_fields=["values"])  # the field reads the clear row and writes it encrypted
    for prop in Properties.objects.filter(owner_type="flowaction", key__in=ENCRYPTED_PROPERTIES):
        if not isinstance(prop.value, str):
            prop.value = encrypt_json(prop.value)
            prop.save(update_fields=["value"])


def decrypt_existing(apps: "Apps", schema_editor: "BaseDatabaseSchemaEditor") -> None:
    FlowAction = apps.get_model("uds", "FlowAction")
    Properties = apps.get_model("uds", "Properties")
    for action in FlowAction.objects.exclude(values__isnull=True):
        # An explicit JSON expression skips the field's encryption, writing the clear document back
        FlowAction.objects.filter(pk=action.pk).update(values=models.Value(action.values, output_field=models.JSONField()))
    for prop in Properties.objects.filter(owner_type="flowaction", key__in=ENCRYPTED_PROPERTIES):
        if isinstance(prop.value, str):
            prop.value = decrypt_json(prop.value)
            prop.save(update_fields=["value"])


class Migration(migrations.Migration):
    dependencies = [
        ("uds", "0054_actionflow_flowaction"),
    ]

    operations = [
        migrations.AlterField(
            model_name="flowaction",
            name="values",
            field=uds.models.action_flow.EncryptedJSONField(blank=True, default=None, null=True),
        ),
        migrations.RunPython(encrypt_existing, decrypt_existing),
    ]
