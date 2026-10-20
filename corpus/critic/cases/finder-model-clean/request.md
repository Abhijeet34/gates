# Decision request: run the review finder on Sonnet

## Recommendation

Run the review finder on Sonnet rather than Opus for work Opus wrote.
At 60,000 input and 4,000 output tokens a review, a Sonnet finder costs 0.24 USD against 0.40 USD on Opus.
Over 400 reviews a month that saves 64 USD, which is the reason for this request.
On the 14 seeded corpus cases each caught the same 8 seeds, so the cheaper model loses nothing on that set.

## Evidence

- Sonnet list price: 3 USD per 1,000,000 input tokens and 15 USD per 1,000,000 output tokens.
- Opus list price: 5 USD per 1,000,000 input tokens and 25 USD per 1,000,000 output tokens.
- Reviews last month: 400.
- Seeded corpus: of 14 seeded cases, the Sonnet and the Opus finder each caught the same 8, case for case.
