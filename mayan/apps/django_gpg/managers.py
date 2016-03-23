from __future__ import absolute_import, unicode_literals

import logging
import os
import shutil
import tempfile

import gnupg

from django.db import models

from .classes import KeyStub, SignatureVerification
from .exceptions import DecryptionError, KeyDoesNotExist, KeyFetchingError
from .literals import KEY_TYPE_PUBLIC, KEY_TYPE_SECRET
from .settings import setting_gpg_path, setting_keyserver

logger = logging.getLogger(__name__)


class KeyManager(models.Manager):
    def decrypt_file(self, file_object):
        temporary_directory = tempfile.mkdtemp()

        os.chmod(temporary_directory, 0x1C0)

        gpg = gnupg.GPG(
            gnupghome=temporary_directory, gpgbinary=setting_gpg_path.value
        )

        decrypt_result = gpg.decrypt_file(file=file_object)

        shutil.rmtree(temporary_directory)

        logger.debug('decrypt_result.__dict__: %s', decrypt_result.__dict__)

        if not decrypt_result.status or decrypt_result.status == 'no data was provided':
            raise DecryptionError('Unable to decrypt file')

        return str(decrypt_result)

    def receive_key(self, key_id):
        temporary_directory = tempfile.mkdtemp()

        os.chmod(temporary_directory, 0x1C0)

        gpg = gnupg.GPG(
            gnupghome=temporary_directory, gpgbinary=setting_gpg_path.value
        )

        import_results = gpg.recv_keys(setting_keyserver.value, key_id)

        if not import_results.count:
            shutil.rmtree(temporary_directory)
            raise KeyFetchingError('No key found')
        else:
            key_data = gpg.export_keys(import_results.fingerprints[0])

            shutil.rmtree(temporary_directory)

            return self.create(key_data=key_data)

    def search(self, query):
        temporary_directory = tempfile.mkdtemp()

        gpg = gnupg.GPG(
            gnupghome=temporary_directory, gpgbinary=setting_gpg_path.value
        )

        key_data_list = gpg.search_keys(
            query=query, keyserver=setting_keyserver.value
        )
        shutil.rmtree(temporary_directory)

        result = []
        for key_data in key_data_list:
            result.append(KeyStub(raw=key_data))

        return result

    def public_keys(self):
        return self.filter(key_type=KEY_TYPE_PUBLIC)

    def private_keys(self):
        return self.filter(key_type=KEY_TYPE_SECRET)

    def verify_file(self, file_object, signature_file=None, key_fingerprint=None, all_keys=False):
        temporary_directory = tempfile.mkdtemp()

        gpg = gnupg.GPG(
            gnupghome=temporary_directory, gpgbinary=setting_gpg_path.value
        )

        # Preload keys
        if all_keys:
            for key in Key.objects.all():
                gpg.import_keys(key_data=key.key_data)
        elif key_fingerprint:
            try:
                key = self.get(fingerprint=key_fingerprint)
            except self.model.DoesNotExist:
                shutil.rmtree(temporary_directory)
                raise KeyDoesNotExist('Specified key for verification not found in keyring')
            else:
                gpg.import_keys(key_data=key.key_data)

        verify_result = gpg.verify_file(file=file_object)

        logger.debug('verify_result.status: %s', verify_result.status)

        if 'no public key' in verify_result.status and not key_fingerprint and not all_keys:
            # File is signed but we need the key for full verification
            try:
                key = self.get(fingerprint__endswith=verify_result.key_id)
            except self.model.DoesNotExist:
                shutil.rmtree(temporary_directory)
                raise KeyDoesNotExist('Signature key is not found in keyring')
            else:
                gpg.import_keys(key_data=key.key_data)
                file_object.seek(0)
                verify_result = gpg.verify_file(file=file_object)

        shutil.rmtree(temporary_directory)

        return SignatureVerification(verify_result.__dict__)