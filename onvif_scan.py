# -*- coding: utf-8 -*-
"""کشف URI استریم‌ها از طریق ONVIF — بدون وابستگی جدید.

استاندارد ONVIF راه رسمی پرسیدن از خود دوربین است که «استریم‌هایت
کجایند؟»: سرویس device (معمولاً پورت ۸۰ یا ۸۸۹۹، مسیر
/onvif/device_service) ← GetCapabilities ← آدرس سرویس media ←
GetProfiles ← GetStreamUri برای هر پروفایل.

خروجی onvif_get_stream_uris: لیست یکتای URIهای RTSP که دوربین معرفی
می‌کند. احرازهویت WS-Security UsernameToken (PasswordDigest) پیاده شده؛
رمز هرگز لاگ نمی‌شود (فقط طول خطاها و URIها).
"""

import base64
import hashlib
import http.client
import os
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

_DEVICE_PATH = "/onvif/device_service"
_PROBE_PORTS = (80, 8899)

_SOAP_ENV = "http://www.w3.org/2003/05/soap-envelope"


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag.rsplit(":", 1)[-1]


def _wsse_header(username: str, password: str) -> str:
    nonce = os.urandom(16)
    created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    raw = nonce + created.encode("utf-8") + password.encode("utf-8")
    digest = base64.b64encode(hashlib.sha1(raw).digest()).decode("ascii")
    return (
        '<s:Header>'
        '<wsse:Security s:mustUnderstand="true" '
        'xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/'
        'oasis-200401-wss-wssecurity-secext-1.0.xsd">'
        "<wsse:UsernameToken>"
        f"<wsse:Username>{username}</wsse:Username>"
        '<wsse:Password Type="http://docs.oasis-open.org/wss/2004/01/'
        'oasis-200401-wss-username-token-profile-1.0#PasswordDigest">'
        f"{digest}</wsse:Password>"
        '<wsse:Nonce EncodingType="http://docs.oasis-open.org/wss/2004/01/'
        'oasis-200401-wss-soap-message-security-1.0#Base64Binary">'
        f"{base64.b64encode(nonce).decode('ascii')}</wsse:Nonce>"
        '<wsu:Created xmlns:wsu="http://docs.oasis-open.org/wss/2004/01/'
        f'oasis-200401-wss-wssecurity-utility-1.0.xsd">{created}</wsu:Created>'
        "</wsse:UsernameToken></wsse:Security></s:Header>"
    )


def _envelope(username: str, password: str, body: str) -> bytes:
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<s:Envelope xmlns:s="{_SOAP_ENV}">'
        f"{_wsse_header(username, password)}"
        f"<s:Body>{body}</s:Body></s:Envelope>"
    )
    return xml.encode("utf-8")


def _post(host: str, port: int, path: str, body: bytes,
          timeout: float) -> bytes:
    conn = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        conn.request("POST", path, body=body,
                     headers={"Content-Type": "application/soap+xml; charset=utf-8"})
        resp = conn.getresponse()
        data = resp.read()
        if resp.status != 200:
            raise OSError(f"HTTP {resp.status}")
        return data
    finally:
        conn.close()


def _split_xaddr(xaddr: str):
    # "http://192.168.1.138:80/onvif/media_service" → ("192.168.1.138", 80, "/onvif/media_service")
    xaddr = xaddr.strip()
    if "://" in xaddr:
        xaddr = xaddr.split("://", 1)[1]
    hostport, _, path = xaddr.partition("/")
    host, _, port = hostport.partition(":")
    return host, int(port) if port else 80, "/" + path


def onvif_get_stream_uris(host: str, username: str, password: str,
                          timeout: float = 2.5, debug_log=None) -> list:
    """URIهای RTSP معرفی‌شده توسط ONVIF دوربین. خالی یعنی پیدا نشد."""
    log = debug_log or (lambda m: None)
    uris = []
    for port in _PROBE_PORTS:
        try:
            body = _envelope(
                username, password,
                '<tds:GetCapabilities xmlns:tds="http://www.onvif.org/ver10/device/wsdl">'
                "<tds:Category>Media</tds:Category></tds:GetCapabilities>")
            data = _post(host, port, _DEVICE_PATH, body, timeout)
        except Exception as e:  # noqa: BLE001
            log(f"ONVIF: پورت {port} جواب نداد ({e})")
            continue
        try:
            root = ET.fromstring(data)
        except ET.ParseError as e:
            log(f"ONVIF: پاسخ نامعتبر از پورت {port} ({e})")
            continue
        media_xaddr = None
        for media in root.iter():
            if _local(media.tag) == "Media":
                for el in media.iter():
                    if _local(el.tag) == "XAddr" and el.text:
                        media_xaddr = el.text.strip()
                        break
                break
        if not media_xaddr:
            log(f"ONVIF: پورت {port} آدرس سرویس media را نداد")
            continue
        log(f"ONVIF: سرویس media در {media_xaddr}")
        try:
            mhost, mport, mpath = _split_xaddr(media_xaddr)
            body = _envelope(
                username, password,
                '<trt:GetProfiles xmlns:trt="http://www.onvif.org/ver10/media/wsdl"/>')
            data = _post(mhost, mport, mpath, body, timeout)
            root = ET.fromstring(data)
            tokens = []
            for el in root.iter():
                if _local(el.tag) == "Profiles":
                    t = el.get("token")
                    if t:
                        tokens.append(t)
            log(f"ONVIF: {len(tokens)} پروفایل پیدا شد")
            for token in tokens:
                body = _envelope(
                    username, password,
                    '<trt:GetStreamUri xmlns:trt="http://www.onvif.org/ver10/media/wsdl">'
                    "<trt:StreamSetup><trt:Stream>RTP-Unicast</trt:Stream>"
                    "<trt:Transport><trt:Protocol>RTSP</trt:Protocol>"
                    "</trt:Transport></trt:StreamSetup>"
                    f"<trt:ProfileToken>{token}</trt:ProfileToken>"
                    "</trt:GetStreamUri>")
                data = _post(mhost, mport, mpath, body, timeout)
                root = ET.fromstring(data)
                for el in root.iter():
                    if _local(el.tag) == "Uri" and el.text:
                        uri = el.text.strip()
                        if uri not in uris:
                            uris.append(uri)
                        break
        except Exception as e:  # noqa: BLE001
            log(f"ONVIF: خطا در گرفتن استریم‌ها ({e})")
            continue
        break  # اولین پورتی که جواب داد کافی است
    for uri in uris:
        log(f"ONVIF: stream URI: {uri}")
    if not uris:
        log("ONVIF: هیچ URI استریمی پیدا نشد")
    return uris
