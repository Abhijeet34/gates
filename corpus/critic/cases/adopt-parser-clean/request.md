# Decision request: adopt a faster YAML parser for config loading

## Recommendation

Adopt the faster YAML parser for loading our configuration.
It parses our 1,200-line config in 4 ms against 30 ms today, 7.5 times faster.
Config loading is 30 ms of a 250 ms startup, 12%, so startup falls by about a tenth.
It has had no release in 26 months and has 14 open crash reports on malformed input, so it sits behind our own loader and is fuzzed against those inputs before it ships.

## Evidence

- Parse time of the 1,200-line config: 30 ms with the current parser, 4 ms with the new one.
- Startup time today: 250 ms.
- Its last release was 26 months ago, and 14 open issues report crashes on malformed input.
