"""Deliver the immutable dispatcher-approved snapshot, never the mutable draft."""
from __future__ import annotations


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
    lines = ['# Pinpoint — approved demo packet',
             f"Packet: {snapshot['id']}", f"Incident: {incident['id']}",
             f"Approved revision: {incident['revision']} at {snapshot['approvedAt']}",
             'Demo only. This does not dispatch emergency help.', '',
             f"Building: {prop.get('name', incident['propertyId'])}",
             f"Address: {prop.get('address', 'Not confirmed')}", '',
             '## Caller location', incident.get('location') or 'Not confirmed', '',
             '## Entrance and route', incident.get('route') or 'Not confirmed', '',
             '## Dispatcher notes', incident.get('notes') or 'None', '',
             '## Not confirmed']
    lines += ['- ' + item for item in incident.get('unconfirmed', [])] or ['None listed']
    if incident.get('packetMarkdown'):
        lines += ['', '## Reviewed agent packet',
                  'The following is the exact reviewed content; its internal status may still say draft.',
                  incident['packetMarkdown']]
    return '\n'.join(lines) + '\n'
