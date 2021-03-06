# generic imports
import logging
from datetime import datetime

# crypto module headers and helpers imports
import mflod.crypto.exceptions as exc
import mflod.crypto.asn1_structures as asn1_dec
from mflod.crypto.constants import Constants as const
from mflod.crypto.log_strings import LogStrings as logstr

# ASN.1 tools imports
from pyasn1.type import univ
from pyasn1.codec.der.encoder import encode as asn1_encode
from pyasn1.codec.der.decoder import decode as asn1_decode

# cryptography connected imports
import hmac
import hashlib
from os import urandom
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.hashes import SHA1
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from cryptography.hazmat.primitives.asymmetric import padding as asym_padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend
from cryptography.exceptions import InvalidSignature, InvalidKey


class Crypto(object):
    """ Class that handles assembly of FLOD protocol message packet

    The main purpose of this class is to provide cryptographic back-end
    for FLOD protocol client to prepare content user wishes to send to
    a transportation.

    The Steps involved are hybrid encryption, HMAC calculation, signing
    etc. The structure of a message packet and some implementation notes
    can be found in module README.md file: https://goo.gl/leWWa4

    Developers:
        - vsmysle (Kyrylo Voronchenko)
        - ddnomad (Artem Fliunt)
        - Sofiya Demchuk

    """

    def __init__(self):
        """ Initialization method """

        # init logger object
        self.logger = logging.getLogger(__name__)
        self.logger.debug(logstr.CRYPTO_CLASS_INIT)

    def assemble_message_packet(self, msg_content, recipient_pk, sign=None):
        """ Assemble FLOD message packet

        @developer: vsmysle

        Assembly involves a creation of DER-encoded ASN.1 structure that
        corresponds to a specification of FLOD message packet format. To
        include optional signing in header block pass a sender_sk named
        argument.

        Also this method handles the assembly of a HEADER block that involves
        putting all the keys and meta-information together.

        :param msg_content:         string message to include into FLOD
                                    message packet
        :param recipient_pk:        instance of cryptography.hazmat.
                                    primitives.asymmetric.rsa.RSAPublicKey
                                    that is a public key of a recipient.
                                    Used to encrypt a header block of the
                                    message packet.
        :param sign=None:           list
                                    [instance of cryptography.hazmat.
                                    primitives.asymmetric.rsa.RSAPrivateKey
                                    that is used to create a signature in the
                                    header block of FLOD message packet,
                                    string PGPKey_ID]

        :return: string DER-encoded ASN.1 structure that is FLOD message packet
                 ready to be sent to a recipient

        :raise: ???

        """
        # enable debug logging
        self.logger.debug("assembly flod message packet")

        # generate key_lst = [iv, aes_key, hmac_key]
        key_lst = self.__get_random_bytes([16, 16, 20])

        # generate content block (AES encrypted )
        content_block = self.__assemble_content_block(msg_content,
                                                      key_lst[1], key_lst[0])

        # generate HMAC block
        hmac_block = self.__assemble_hmac_block(asn1_encode(content_block),
                                                key_lst[2])

        # calculate the maximum length of RSA encryption
        rsa_max_len = self.__get_rsa_max_bytestring_size(recipient_pk.key_size)

        # creating instance of AlgorithmIdentifier class for RSA signing
        algo_identifier = asn1_dec.AlgorithmIdentifier()

        # set default parameters to univ.Null()
        algo_identifier['parameters'] = univ.Null()

        if sign:
            # logger for existence of sign list
            self.logger.info("sign list is present")

            # generate signature using sender secret key and PGPKeyID
            signature = self.__sign_content(key_lst[1]+key_lst[2], sign[0])

            # assign PGPKeyID to a variable
            pgp_key_id = sign[1]

            # setting oid for the rsassa-pss
            algo_identifier['algorithm'] = const.RSASSA_PSS_OID

        else:
            # logger for existence of sign list
            self.logger.info("sign list is not present")

            # if there is no sign list - generate random signature
            signature = urandom(rsa_max_len)

            # generating random PGPKeyID
            pgp_key_id = urandom(8)

            # setting signature oid to zeros
            algo_identifier['algorithm'] = const.NO_SIGN_OID

        # creating instance of MPHeader class
        mp_header = asn1_dec.MPHeader()

        # setting indentificationString to constant - FLOD
        mp_header['identificationString'] = const.IS

        # setting AlgorithmIdentifier as a parameter of MPHeader
        mp_header['signatureAlgorithm'] = algo_identifier

        # setting PGPKeyID that was defined above
        mp_header['PGPKeyID'] = pgp_key_id

        # setting signature that was calculated previously
        mp_header['signature'] = signature

        # setting HMACKey from the generated keys list
        mp_header['HMACKey'] = key_lst[2]

        # setting AESKey from the generated keys list
        mp_header['AESKey'] = key_lst[1]

        # encoding header into ASN.1 DER-encoded structure
        encoded_mp_header = asn1_encode(mp_header)

        # encrypting parts of encoded header with RSA
        # we encrypt several part due to restriction of the RSA max encryption
        # length

        enc_header = bytes()
        for rsa_block in [encoded_mp_header[i:i+rsa_max_len] for i in
                          range(0, len(encoded_mp_header), rsa_max_len)]:
            enc_header += self.__encrypt_with_rsa(rsa_block, recipient_pk)

        # creating instance of AlgorithmIdentifier for RSA encryption OID
        rsa_algo_identifier = asn1_dec.AlgorithmIdentifier()

        # setting the OID for id-rsaes-oaep
        rsa_algo_identifier['algorithm'] = const.ID_RSAES_OAEP

        # setting default parameters to univ.Null()
        rsa_algo_identifier['parameters'] = univ.Null()

        # creating the instance of MPHeaderContainer class
        mp_header_container = asn1_dec.MPHeaderContainer()

        # setting AlgorithmIdentifier
        mp_header_container['encryptionAlgorithm'] = rsa_algo_identifier

        # set encrypted header to the OCTET STRING
        mp_header_container['encryptedHeader'] = enc_header

        # creating the instance of MessagePacket class
        message_packet = asn1_dec.MessagePacket()

        # setting the version of our protocol that is taken from constants
        message_packet['protocolVersion'] = const.PROTOCOL_VERSION

        # setting the instance of MPHeaderContainer to the header block
        message_packet['headerBlock'] = mp_header_container
        message_packet['hmacBlock'] = hmac_block
        message_packet['contentBlock'] = content_block

        return asn1_encode(message_packet)

    def disassemble_message_packet(self, msg_packet, key_manager):
        """ Attempt to disassemble FLOD message packet that was received

        @developer: ddnomad

        Disassembly involves test decryption of header block with all available
        private keys of a user. If decryption was successful then the message
        was addressed to the user. This does not means though that the
        signature verification will succeed as well as HMAC integrity check.

        On successful decryption the method returns a recovered message
        together with a supplementary exit code which determines conditions
        that occur during disassembly process. The code can be one of the
        following integers:

            - 0: indicates that signature verification was successful with a
              known sender PGPKeyID
            - 1: indicates that signature verification was successful but the
              key used was not a PGP key (but it exists in a user key chain)
            - 2: indicates that decryption was successful but the message was
              not signed by a sender
            - 3: indicates that the signature authenticity cannot be
              established due to an absence of a corresponding public key

        :param msg_packet:          string DER-encoded ASN.1 structure of FLOD
                                    message packet to decrypt
        :param key_manager:         instance of mflod.crypto.key_manager.
                                    KeyManager that should implement two
                                    mandatory methods:
                                        - yield_keys() which return all user
                                          private keys (both PGP and plain
                                          ones) one by one (generator). The
                                          method has to return instances of
                                          cryptography.hazmat.primitives.
                                          asymmetric.rsa.RSAPrivateKey
                                        - get_pk_by_pgp_id(pgp_id) which
                                          attempts to find a matching to an
                                          string input ID PGP key. If the key
                                          was found - return an instance of
                                          cryptography.hazmat.primitives.
                                          asymmetric.rsa.RSAPublicKey. If there
                                          is not such key - return None. If the
                                          ID passed is all 0s - return a list
                                          of all user plain RSA public keys.

        :return: one of the following lists (see supplementary exit codes
                 paragraph for details):
                    - [timestamp, dec_msg, 0, pgp_key_id]
                    - [timestamp, dec_msg, 1, sign_pk]
                    - [timestamp, dec_msg, 2]
                    - [timestamp, dec_msg, 3]
                The values in lists are the following:
                    - timestamp:    instance of datetime.datetime time when
                                    the message was composed by a sender
                    - dec_msg:      string decryption of a message received
                    - pgp_key_id:   string PGPKeyID of a public key that
                                    verified a signature
                    - sign_pk:      an instance of cryptography.hazmat.
                                    primitives.rsa.RSAPublicKey that verified a
                                    signature

        :raise mflod.crypto.exceptions.NoMatchingRSAKeyForMessage,
               mflod.crypto.exceptions.SignatureVerificationFailed,
               mflod.crypto.exceptions.HMACVerificationFailed
        """

        # log entry
        self.logger.debug(logstr.DISASSEMBLE_MESSAGE_PACKET_CALL)

        # decode message packet from DER and get header block
        message_packet_asn1 = asn1_decode(msg_packet)
        header_block_asn1 = message_packet_asn1[0][1]

        # get encrypted from a header container
        mp_header_ct = bytes(header_block_asn1[1])

        # entering brute-force loop
        self.logger.debug(logstr.ATTEMPT_DECRYPT_HEADER)

        # try to decrypt a header with all available user keys
        for user_sk in key_manager.yield_keys():

            # determine a size of a current user secret key
            key_size = user_sk.key_size // 8

            # get decrypted first RSA block of MPHeader
            try:
                mp_header_pt_init_block = self.__decrypt_with_rsa(
                        mp_header_ct[:key_size], user_sk)
            except (InvalidKey, ValueError):

                # probably key size doesn't match
                self.logger.debug(logstr.INVALID_RSA_KEY)
                continue

            # calculate identification string offset
            offset = self.__calculate_der_id_string_offset(
                    mp_header_pt_init_block)

            # check whether id string matches
            if not mp_header_pt_init_block[offset:offset + 4] == \
                    bytes(const.IS, 'utf-8'):

                # key doesn't fit
                self.logger.debug(logstr.WRONG_RSA_KEY)
                continue

            # found a matching key - message can be decrypted
            else:

                self.logger.info(logstr.MESSAGE_FOR_USER)

                # init exit code (optimistic)
                exit_code = 0
                signer_info = None

                # create a variable to hold the MPHeader plaintext
                mp_header_pt = mp_header_pt_init_block

                # decrypt the whole MPHeader DER
                for rsa_block in [mp_header_ct[i:i+key_size] for i in
                                  range(key_size, len(mp_header_ct), key_size)]:

                    # append decrypted chunks
                    mp_header_pt += self.__decrypt_with_rsa(rsa_block, user_sk)

                # decode MPHeader from DER
                mp_header_pt_asn1 = asn1_decode(mp_header_pt)

                # determine whether the header was signed
                sign_oid = str(mp_header_pt_asn1[0][1][0])
                pgp_key_id = str(mp_header_pt_asn1[0][2])
                signature = bytes(mp_header_pt_asn1[0][3])
                hmac_key = bytes(mp_header_pt_asn1[0][4])
                aes_key = bytes(mp_header_pt_asn1[0][5])
                sign_content = hmac_key + aes_key

                # there is a signature
                if sign_oid != const.NO_SIGN_OID:

                    self.logger.info(logstr.MESSAGE_IS_SIGNED)

                    # get signer public key
                    signer_cands = key_manager.get_pk_by_pgp_id(pgp_key_id)

                    # there is a public key
                    if isinstance(signer_cands, RSAPublicKey):
                        if self.__verify_signature(signature, signer_cands,
                                                   sign_content):
                            signer_info = pgp_key_id
                        else:
                            # TODO: more verbose
                            raise exc.SignatureVerificationFailed("")

                    # nothing found for this PGP ID
                    elif signer_cands is None:
                        self.logger.warn(logstr.SIGN_CANNOT_VERIF)
                        exit_code = 3

                    # the PGP ID is zeros so signer used non-PGP key
                    # get_pk_byid_func returned a list of all user's
                    # non-PGP public keys (from an internal key chain
                    else:

                        # just in case
                        assert(isinstance(signer_cands, tuple))

                        self.logger.info(logstr.NON_PGP_KEY_SIGN)

                        # brute over all user non-PGP keys in attempt to verify
                        for cand_key in signer_cands:

                            # again just in case
                            assert(isinstance(cand_key, RSAPublicKey))

                            verif_ok = self.__verify_signature(signature,
                                                               cand_key,
                                                               sign_content)

                            if verif_ok:
                                break

                        # update exit code state
                        if verif_ok:
                            exit_code = 1
                            signer_info = cand_key
                        else:
                            exit_code = 3

                # there is no signature in MPHeader
                else:

                    self.logger.info(logstr.NOT_SIGNED_MESSAGE)
                    exit_code = 2

                    # retrieve MPHMACContainer and MPContentContainer
                    mp_hmac_container = message_packet_asn1[0][2]
                    mp_content_container = message_packet_asn1[0][3]

                    # verify hmac
                    hmac_ver_res = self.__verify_hmac(mp_hmac_container,
                            hmac_key, asn1_encode(mp_content_container))

                    if not hmac_ver_res:
                        # TODO: more verbose str
                        raise exc.HMACVerificationFailed("")

                    # all checks were successful - decrypt content
                    timestamp, message = self.__disassemble_content_block(
                            mp_content_container, aes_key)

                    self.logger.info(logstr.MSG_CONTENT_WAS_RECOVERED)

                    # return correct result
                    if signer_info:
                        return timestamp, message, exit_code, signer_info
                    return timestamp, message, exit_code

        # TODO: create more verbose exception
        self.logger.info(logstr.MESSAGE_NOT_FOR_USER)
        raise exc.NoMatchingRSAKeyForMessage("")

    def __calculate_der_id_string_offset(self, der):
        """ Determine an offset to identification string in header fragment

        :param der: DER-encoded fragment of MPHeader ASN.1 structure

        :return: integer offset to an indentification string

        """

        # bitmasks declaration
        MSB_MASK = 0x80
        LEN_SPEC_MASK = 0x7F

        len_spec = der[1]
        if len_spec & MSB_MASK == MSB_MASK:
            len_of_len = len_spec & LEN_SPEC_MASK
            return len_of_len + 4
        return 4

    def __assemble_content_block(self, content, key, iv):
        """ Create an ASN.1 DER-encoded structure of a content block

        @developer: ddnomad

        The corresponding ASN.1 structure from a documentation is
        MPContentContainer

        :param content: string content to encapsulate
        :param key:     bytes AES key to use for encryption
        :param iv:      bytes CBC mode initialization vector

        :return: string DER-encoding of MPContentContainer ASN.1 structure

        """

        # logger entry
        self.logger.debug(logstr.ASSEMBLE_CONTENT_BLOCK_CALL)

        # create an ASN.1 structure of MPContent and DER-encode it
        mp_content_pt = asn1_dec.MPContent()
        mp_content_pt['timestamp'] = datetime.utcnow(). \
            strftime(const.TIMESTAMP_FORMAT)
        mp_content_pt['content'] = content
        mp_content_pt_der = asn1_encode(mp_content_pt)

        # encrypt MPContent DER
        mp_content_ct = self.__encrypt_with_aes(mp_content_pt_der, key, iv)

        # wrap MPContent into MPContentContainer
        mp_content_container = asn1_dec.MPContentContainer()
        mp_content_container['initializationVector'] = iv
        mp_content_container['encryptionAlgorithm'] = \
            self.__get_asn1_algorithm_identifier(const.AES_128_CBC_OID)
        mp_content_container['encryptedContent'] = mp_content_ct

        # encode MPContentContainer and return it
        return mp_content_container

    def __disassemble_content_block(self, content, key):
        """ Decrypt and decode content from a content block

        @developer: ddnomad

        :param content: instance of MPContentContainer class
        :param key:     string AES key to be used for decryption

        :return: list of the following values:
                    [0] datetime.datetime timestamp object
                    [1] string decrypted message

        """

        # log entry
        self.logger.debug(logstr.DISASSEMBLE_CONTENT_BLOCK_CALL)

        # decode MPContentContainer from DER
        # TODO: try-except in a case when decoding failed
        mp_content_container_asn1 = content
        # recover values that are necessary for decryption
        # TODO: verify encryptionAlgorithm OID
        iv = bytes(mp_content_container_asn1[0])
        enc_content = bytes(mp_content_container_asn1[2])

        # decrypt DER-encoded MPContent
        mp_content_pt_der = self.__decrypt_with_aes(enc_content, key, iv)

        # recover timestamp and message from DER-encoded MPContent
        mp_content_pt_asn1 = asn1_decode(mp_content_pt_der)
        timestamp = datetime.strptime(str(mp_content_pt_asn1[0][0]),
                                      const.TIMESTAMP_FORMAT)
        message = str(mp_content_pt_asn1[0][1])

        # return the resulting data
        return timestamp, message

    def __assemble_hmac_block(self, content, key):
        """ Produce HMAC block ASN.1 structure (MPHMACContainer)

        @developer: vsmysle

        :param content: string DER-encoded content generate HMAC of and
                        encapsulate into HMAC FLOD block
        :param key:     bytes key to use for HMAC generation

        :return: DER-encoded ASN.1 structure that encapsulates HMAC
                 block

        """
        # TODO: add exceptions

        self.logger.debug("producing HMAC block with ASN.1 structure")

        # calculating hmac digest of content
        digest = self.__generate_hmac(content, key)

        # oid for SHA1 hash function
        oid = const.SHA1_OID

        # creating instance of AlgorithmIdentifier class
        ai = asn1_dec.AlgorithmIdentifier()

        # setting corresponding parameters
        ai['algorithm'] = oid
        ai['parameters'] = univ.Null()

        # creating instance of MPHMACContainer class
        hmac_block = asn1_dec.MPHMACContainer()

        # setting corresponding parameters
        hmac_block['digestAlgorithm'] = ai
        hmac_block['digest'] = digest

        return hmac_block

    def __verify_hmac(self, hmac_blk, key, content_blk):
        """ Verify content HMAC


        @developer: vsmysle

        :param hmac_blk:        instance of MPHMACContainer class
        :param key:             bytes HMAC secret key
        :param content_blk:     bytes DER-encoded ASN.1 structure of content
                                block

        :return: bool verification result

        """
        # TODO: add exceptions

        self.logger.debug("verifying  HMAC")

        # calculation of the HMAC digest for received content block
        hmac_of_content_blk = self.__generate_hmac(content_blk, key)

        # get digest from the HMAC block
        digest = hmac_blk[1]

        # compering calculated digest from content block and
        # digest from instance of MPHMACContainer class

        if digest == hmac_of_content_blk:
            self.logger.info("successful HMAC verification")
            return True
        self.logger.warning("HMAC verification failed!")
        return False

    def __generate_hmac(self, content, key):
        """ Generate HMAC for in input content and key

        @developer: vsmysle

        :param content: string DER-encoded content to produce digest of
        :param key:     bytes key to use for HMAC

        :return: string HMAC of the input content

        """
        # TODO: add exceptions

        self.logger.debug("generation HMAC for input content")

        # generating instance of HMAC with sha1 hash function
        hmac_digest = hmac.new(key, None, hashlib.sha1)

        # feed the content to generated HMAC instance
        hmac_digest.update(content)
        return hmac_digest.digest()

    def __encrypt_with_aes(self, content, key, iv):
        """ Encrypt content with AES-128-CBC (with PCKS#7 padding)

        @developer: ddnomad

        :param content: bytes DER-encoded MPContent ASN.1 structure to encrypt
        :param key:     bytes key to use for encryption
        :param iv:      bytes CBC mode initialization vector

        :return: string encryption of an input content

        """

        # log entry
        self.logger.debug(logstr.AES_ENC_CALL)

        # pad MPContent with PKCS#7
        padder = padding.PKCS7(const.AES_BLOCK_SIZE).padder()
        padded_content = padder.update(content) + padder.finalize()

        # initialize AES cipher instance
        backend = default_backend()
        aes = Cipher(algorithms.AES(key), modes.CBC(iv),
                     backend=backend).encryptor()

        # encrypt padded content
        content_ct = aes.update(padded_content) + aes.finalize()

        # return the resulting ciphertext
        return content_ct

    def __decrypt_with_aes(self, content, key, iv):
        """ Decrypt AES-128-CBC encrypted content (PCKS#7 padded)

        @developer: ddnomad

        :param content: bytes ciphertext of MPContent ASN.1 structure
        :param key:     bytes AES secret key
        :param iv:      bytes CBC mode initialization vector

        :return: string decrypted DER-encoded MPContent ASN.1 structure

        """

        # log entry
        self.logger.debug(logstr.AES_DEC_CALL)

        # initialize AES cipher instance
        backend = default_backend()
        aes = Cipher(algorithms.AES(key), modes.CBC(iv),
                     backend=backend).decryptor()

        # decrypt content
        dec_content = aes.update(content) + aes.finalize()

        # unpad content
        unpadder = padding.PKCS7(const.AES_BLOCK_SIZE).unpadder()
        dec_content_unpadded = unpadder.update(dec_content) + \
            unpadder.finalize()

        # return the resulting plaintext content
        return dec_content_unpadded

    def __encrypt_with_rsa(self, content, recipient_pk):
        """ Encrypt content with RSAES-OAEP scheme

        @developer: vsmysle

        This method handles an encryption of a *single* RSA block with a
        specified above scheme. It does not handle splitting of a header into
        several blocks. It has to be done by other method that would use this
        one only for single block encryption purpose.

        TODO: what is a maximum size of a content that can be padded and
        encrypted given a particular size of RSA key?

        :param content:         bytes content to encrypt (probably a part of
                                ASN.1 DER-encoded MPHeader block)
        :param recipient_pk:    instance of cryptography.hazmat.primitives.rsa
                                .RSAPublicKey to use for a content encryption

        :return: string encryption of an input content

        """

        # TODO: add exceptions
        self.logger.debug("rsa encryption")

        ciphertext = recipient_pk.encrypt(
            content, asym_padding.OAEP(
                mgf=asym_padding.MGF1(algorithm=SHA1()),
                algorithm=SHA1(),
                label=None
            )
        )
        self.logger.info("encrypted")
        return ciphertext

    def __decrypt_with_rsa(self, content, user_sk):
        """ Decrypt RSAES-OAEP encrypted content (single block)

        @developer: vsmysle

        This method decrypts a single RSA ciphertext block only

        :param content: bytes content to decrypt
        :param user_sk: instance of cryptography.hazmat.primitives.rsa
                        .RSAPrivateKey to use for a decryption

        :return: string decryption of an input content

        """
        # TODO: add exceptions

        self.logger.debug("rsa decryption")
        try:
            plaintext = user_sk.decrypt(
                content, asym_padding.OAEP(
                    mgf=asym_padding.MGF1(algorithm=SHA1()),
                    algorithm=SHA1(),
                    label=None
                )
            )
        except InvalidKey:
            self.logger.warning("Invalid key!")
            return
        return plaintext

    def __sign_content(self, content, user_sk):
        """ Produce a signature of an input content using RSASSA-PSS scheme

        @developer: vsmysle

        :param content: bytes content to sign
        :param user_sk: instance of cryptography.hazmat.primitives.rsa.
                        RSAPrivateKey

        :return: bytes of signature of the input content

        """

        # TODO: add exceptions

        self.logger.debug("generating a signature of an input content")
        # creating signer that will sign our content
        try:
            signer = user_sk.signer(
                # we use RSASSA-PSS padding for the signature scheme
                asym_padding.PSS(
                    mgf=asym_padding.MGF1(SHA1()),
                    salt_length=asym_padding.PSS.MAX_LENGTH
                ),
                SHA1()
            )
        except InvalidKey:
            self.logger.warning("Invalid key!")
            return
        signer.update(content)
        signature = signer.finalize()
        self.logger.info("signature generation finished")
        return signature

    def __verify_signature(self, signature, signer_pk, content):
        """ Verify RSASSA-PSS signature

        @developer: vsmysle

        :param signature: signature bytes to verify
        :param signer_pk: instance of cryptography.hazmat.primitives.
                          rsa.RSAPublicKey that is a public key of a signer
        :param content:   content to verify a signature of

        :return: bool verification result

        """
        self.logger.debug("starting signature verification routine")
        try:
            signer_pk.verify(
                signature,
                content,
                asym_padding.PSS(
                    mgf=asym_padding.MGF1(SHA1()),
                    salt_length=asym_padding.PSS.MAX_LENGTH
                ),
                SHA1()
            )
        except InvalidSignature:
            self.logger.warn("signature verification failed")
            return False
        self.logger.info("signature OK")
        return True

    def __get_asn1_algorithm_identifier(self, oid_str):
        """ Generate ASN.1 structure for algorithm identifier

        @developer: vsmysle

        :param oid_str: string OID to encapsulate

        :return: pyasn1.type.univ.Sequence object

        """

        # TODO: add exceptions

        # log entry
        self.logger.debug("creating AlgorithmIdentifier ASN.1 "
                          "structure with OID=%s" % oid_str)

        # create the instance of AlgorithmIdentifier
        ai = asn1_dec.AlgorithmIdentifier()

        # set corresponding parameters
        ai['algorithm'] = oid_str
        ai['parameters'] = univ.Null()

        # return the result
        return ai

    def __get_random_bytes(self, spec_lst):
        """ Generate random bytes

        @developer: vsmysle

        :param spec_lst: list of integers that is lengths of bytestings to
                         return

        :return: list of random bytes with lengths corresponding to the
                 ones from a spec list

        """

        # TODO: add exception for negative integers

        self.logger.debug("generating random bytes")
        return [urandom(i) for i in spec_lst]

    def __get_rsa_max_bytestring_size(self, key_size):
        """ Helper function that says how many bytes you can encrypt
            with RSA

        @developer: vsmysle

        :param key_size: size of the RSA key

        :return: integer specifying how many bytes you can encrypt using
                 RSA key with specified key size

        """
        return key_size // 8 - 42
