from hook_detector.camera_discovery import _parse_probe_match


def test_parse_onvif_probe_match_returns_local_camera_metadata():
    response = b"""<e:Envelope xmlns:e="urn:soap" xmlns:d="urn:discovery">
      <e:Body><d:ProbeMatches><d:ProbeMatch>
        <d:Types>dn:NetworkVideoTransmitter</d:Types>
        <d:XAddrs>http://192.168.10.24/onvif/device_service</d:XAddrs>
        <d:Scopes>onvif://www.onvif.org/name/Generic%20Camera
          onvif://www.onvif.org/hardware/ExampleCorp</d:Scopes>
      </d:ProbeMatch></d:ProbeMatches></e:Body>
    </e:Envelope>"""

    assert _parse_probe_match(response, "192.168.10.24") == {
        "ip": "192.168.10.24",
        "manufacturer": "ExampleCorp",
        "model": "Generic Camera",
        "onvif_endpoint": "http://192.168.10.24/onvif/device_service",
        "poe_switch_port": None,
    }


def test_discovery_ignores_public_sources_invalid_xml_and_entity_payloads():
    response = b"<Envelope><Types>NetworkVideoTransmitter</Types><XAddrs>http://192.168.10.24/onvif</XAddrs></Envelope>"
    assert _parse_probe_match(response, "8.8.8.8") is None
    assert _parse_probe_match(b"not xml", "192.168.10.24") is None
    assert _parse_probe_match(
        b"<!DOCTYPE x [<!ENTITY x 'expanded'>]><Envelope/>",
        "192.168.10.24",
    ) is None