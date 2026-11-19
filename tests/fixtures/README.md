# Native FLP Test Fixture Provenance

The binary **native_flp_scaffold.flp** (46,688 bytes) is test-only.
SHA256: 9449554bbd72861b06aed33731372b61ccf76b9b84c467b4f9d9e5764c1cca98.

It is a synthetic composite, NOT an FL Studio-saved or app-verified
output. It is created by replacing the empty native Playlist event from
the MIT-licensed dawHub/flpdiff FL Studio 25.2.4 fixture
tests/corpus/re_base/fl25/base_empty.flp, SHA256
1b396047ddf61102a906e99cd01223c8099ee3e1e6c8ee01c2275ae225d6fd23,
with the Playlist event from the MIT-licensed clarkipeng/flpkit
tests/golden/playlist-template-tail-inherited.flp, SHA256
20102c8c8f3202e82613bdfe194813f4a40674331be3cc20142a1486fc779a8.
Only the Playlist event and enclosing FLdt length were changed.

Original repositories:
- https://github.com/dawhubapp/flpdiff (MIT)
- https://github.com/clarkipeng/flpkit (MIT)

No GPL source was incorporated. The fixture only tests source->FLP binary
writer->offline readback. No real FL Studio runtime test is implied.
