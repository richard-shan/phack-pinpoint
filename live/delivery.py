"""Deliver the immutable dispatcher-approved snapshot, never the mutable draft."""
from __future__ import annotations

import re


def approved_packet(console, incident):
    decision = incident.get('decision') or {}
    if (incident.get('status') != 'approved' or decision.get('kind') != 'approved'
            or decision.get('revision') != incident.get('revision') or not incident.get('packetId')):
        return None
    snapshot = console.packet(incident['packetId'])
    saved = snapshot['incident']
    if (saved['id'] != incident['id'] or saved.get('revision') != decision['revision']
            or saved.get('decision') != decision or saved.get('status') != 'approved'):
        return None
    # Unattended evaluation must never send an external packet.
    if saved.get('notes', '').startswith('AUTO-APPROVED by the run'):
        return None
    current = console.get(incident['id'])
    if current.get('packetId') != snapshot['id'] or current.get('decision') != decision:
        return None
    return snapshot


def packet_text(snapshot):
    incident, prop = snapshot['incident'], snapshot['property']
    loc = (incident.get('channel') or {}).get('location') or {}
    lines = ['# Pinpoint — location and route',
             f"Incident: INC-{incident['id'][4:10].upper()}",
             f"Confirmed: {snapshot['approvedAt']}", f"Reference: {snapshot['id']}", '',
             f"Address: {loc.get('address') or prop.get('address', '')}",
             f"Building: {prop.get('name', incident['propertyId'])}", '',
             '## Caller location', re.sub(r'^Scan \d+ · ', '', incident.get('location') or '') or 'Unknown', '',
             '## Entry and route', incident.get('route') or 'No route', '',
             '## Notes', incident.get('notes') or 'None', '',
             '## Verify']
    lines += ['- ' + item for item in incident.get('unconfirmed', [])] or ['None']
    if incident.get('packetMarkdown'):
        lines += ['', '## Reviewed agent packet', incident['packetMarkdown']]
    return '\n'.join(lines) + '\n'
