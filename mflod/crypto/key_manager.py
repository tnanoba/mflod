import logging
import gnupg
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


class KeyManager(object):
    """
    Class manages operations on user GPG keys

    Specifically, goal is to implement and provide gnupg_wrapper.py (GnuPGWrapper) functionality,
    generate plain RSA key pair and support helper methods. Also convert and provide PGP keys to
    a cryptography lib key instances.

    Developers:
        - Tornike Nanobashvili
    """

    def __init__(self):
        """
        Initialize KeyManager class

        Initialization processes:
            - Defines GnuPG instance
            - Defines logging basic configuration
        """
        self.gpg = gnupg.GPG()

        logging.basicConfig(filename='./logs/key_manager.log', level=logging.DEBUG)

        self.logger = logging.getLogger('KeyManager')
        self.logger.debug('KeyManager instance is being created.')

    def gen_rsa_key(self, key_length=2048, user_name='Auto Generated Key', user_comment='Generated by KeyManager',
                    user_email=''):
        """
        Generates RSA key pair

        :param key_length: int (defaults to 2048 bits)
        :param user_name: str (defaults to Auto Generated Key)
        :param user_comment: str (defaults to Generated by KeyManager)
        :param user_email: str (defaults to <username>@<hostname> of local environment)

        :return: str (RSA key fingerprint (SHA1))
        """
        try:
            input_data = self.gpg.gen_key_input(key_type='RSA', key_length=key_length, name_real=user_name,
                                                name_comment=user_comment, name_email=user_email)
            key = self.gpg.gen_key(input_data)

            self.logger.info('RSA ' + '(' + str(key_length) + ' bits) key pair is being generated. Fingerprint: ' +
                             str(key))

            return key.fingerprint
        except Exception as ERROR:
            self.logger.error(ERROR)

    def del_rsa_key(self, fingerprint):
        """
        Deletes RSA key pair of provided fingerprint

        :param fingerprint: str (key HEX SHA1 fingerprint)
        :return: void
        """
        try:
            self.gpg.delete_keys(fingerprint, True)
            self.gpg.delete_keys(fingerprint, False)
        except Exception as ERROR:
            self.logger.error(ERROR)

    def gen_plain_rsa_key(self, key_size=2048):
        """
        Generates RSA key pair based on provided key_size
        and returns dict with public_key and private_key attributes.

        Response example:
            {
                "public_key": "-----BEGIN PUBLIC KEY-----\n...\n-----END PUBLIC KEY-----\n",
                "private_key" "-----BEGIN RSA PRIVATE KEY-----\n...\n-----END RSA PRIVATE KEY-----\n"
            }

        :param key_size: int
        :return: dict
        """
        try:
            # Generates RSA key pair
            key = rsa.generate_private_key(
                public_exponent=65537,
                key_size=key_size,
                backend=default_backend()
            )

            # Serializes private key
            private_key = key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption()
            ).decode('utf-8')

            # Serializes public key
            public_key = key.public_key().public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo
            ).decode('utf-8')

            response = {
                "public_key": public_key,
                "private_key": private_key,
            }

            self.logger.info('Plain RSA (' + str(key_size) + ' bits) key pair is being generated: ' + str(response))

            return response
        except Exception as ERROR:
            self.logger.error(ERROR)
