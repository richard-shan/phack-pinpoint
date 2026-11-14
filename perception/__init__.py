"""Perception lane (Person B): pixels to facts.

    from perception.match import match_photo
    from perception.stubs import detect_site_features, node_attributes
"""
import socket

# IPv6 is broken on the venue network: Python waits out a full timeout per IPv6
# address before falling back (about 40 s per request). Resolve IPv4 only.
if not getattr(socket, "_pinpoint_ipv4", False):
    _real_getaddrinfo = socket.getaddrinfo

    def _ipv4_getaddrinfo(host, port, family=0, *args, **kwargs):
        return _real_getaddrinfo(host, port, socket.AF_INET, *args, **kwargs)

    socket.getaddrinfo = _ipv4_getaddrinfo
    socket._pinpoint_ipv4 = True
