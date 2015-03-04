# -*- coding: utf8 -*-
import logging
from time import time
from socket import socket
from select import select
from Crypto.Random import random

from error import *
from crypto import CRC32, AES_IGE, SHA1


class TcpSession:
    def __init__(self):
        self.client_seq = 0
        self.data = b''
    
    def Connect(self, host, port):
        self.sock = socket()
        self.sock.connect((host, port))
    
    def Receive(self, timeout):
        if len(self.data) < 4 or len(self.data) < int.from_bytes(self.data[:4], 'little'):
            rlist, _, _ = select((self.sock,), (), (), timeout)
            if len(rlist) == 0:
                return
            data = self.sock.recv(4096)
            if len(data) == 0:
                raise ConnectionError("Connection closed")
            self.data += data
        data_len = int.from_bytes(self.data[:4], 'little')
        if len(self.data) < data_len:
            return
        data = self.data[0:data_len]
        self.data = self.data[data_len:]
        if int.from_bytes(data[-4:], 'little') != CRC32(data[:-4]):
            return
        seq = int.from_bytes(data[4:8], 'little')
        self.server_seq = seq
        return data[8:-4]
    
    def Send(self, data):
        length = len(data) + 12
        data = length.to_bytes(4, "little") + self.client_seq.to_bytes(4, "little") + data
        data = data + CRC32(data).to_bytes(4, "little")
        self.client_seq += 1
        # TODO: сделать отложенную отправку
        while len(data) != 0:
            ln = self.sock.send(data)
            data = data[ln:]


class AES_IGE_SESSION(AES_IGE):
    def __init__(self, msg_key, auth_key, dir):
        if dir == "out":
            x = 0
        elif dir == "in":
            x = 8
        else:
            raise ValueError("Invalid dir value: {}".format(dir))
        sha1_a = SHA1(msg_key + auth_key[x:x+32])
        sha1_b = SHA1(auth_key[32+x:32+x+16] + msg_key + auth_key[48+x, 48+x+16])
        sha1_с = SHA1(auth_key[64+x:64+x+32] + msg_key)
        sha1_d = SHA1(msg_key + auth_key[96+x:96+x+32])
        aes_key = sha1_a[0:8] + sha1_b[8:20] + sha1_c[4:16]
        aes_iv = sha1_a[8:20] + sha1_b[0:8] + sha1_c[16:20] + sha1_d[0:8]
        super().__init__(aes_key, aes_iv)


class CryptoSession(TcpSession):
    def __init__(self):
        super().__init__()
        self.message_id = 0
        self.time_offset = 0
        self.session_id = random.getrandbits(64).to_bytes(8, 'big')
        self.seq_no = 0
    
    def getMessageId(self):
        msg_id = int((time() + self.time_offset)  * (1 << 30)) * 4
        if self.message_id >= msg_id:
            self.message_id += 4
        else:
            self.message_id = msg_id
        return self.message_id
    
    def Receive(self, timeout):
        data = super().Receive(timeout)
        if data is None:
            return None
        logging.debug("Recv data: {}".format(self.Hex(data)))
        auth_key_id = data[0:8]
        if auth_key_id == b'\0\0\0\0\0\0\0\0':
            message_id = int.from_bytes(data[8:16], 'little')
            message_len = int.from_bytes(data[16:20], 'little')
            return data[20:]
        else:
            auth_key_id = data[0:8]
            if auth_key_id != self.auth_key_id:
                raise SecurityError('Invalid auth key id')
            msg_key = data[8:24]
            aes_ige = AES_IGE_SESSION(msg_key, self.auth_key)
            data = aes_ige.decrypt(data[24:])
            if msg_key != SHA1(data)[-16:]:
                raise SecurityError('Invalid msg key')
            salt = data[0:8]
            session_id = data[8:16]
            message_id = int.from_bytes(data[16:24], 'little')
            # TODO: проверить message_id
            seq_no = int.from_bytes(data[24:28], 'little')
            message_len = int.from_bytes(data[28:32], 'little')
            return data[32:32+message_len]
        
    def Send(self, data, encrypted=True):
        if encrypted:
            data = self.salt + self.session_id + self.getMessageId().to_bytes(8, "little") + (self.seq_no*2).to_bytes(4, 'little') + len(data).to_bytes(4, 'little') + data
            self.seq_no += 1
            msg_key = SHA1(data)[-16:]
            aes_ige = AES_IGE_SESSION(msg_key, self.auth_key, "out")
            data = self.auth_key_id + msg_key + aes_ige.encrypt(data)
        else:
            data = b'\0\0\0\0\0\0\0\0' + self.getMessageId().to_bytes(8, "little") + len(data).to_bytes(4, "little") + data
        logging.debug("Send data: {}".format(self.Hex(data)))
        return super().Send(data)
    
    def __setattr__(self, name, value):
        if name == "auth_key":
            self.auth_key_id = SHA1(self.auth_key)[-8:]
        return super().__setattr__(name, value)
    
    def Hex(self, data):
        return ''.join(('\n\t{:03x}0 |'.format(i) + ''.join((' {:02x}'.format(int.from_bytes(data[i*16+j:i*16+j+1], 'big')) for j in range(16) if i*16+j < len(data))) for i in range((len(data)-1)//16+1)))

