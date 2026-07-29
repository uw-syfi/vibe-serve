# ExactMap controlled long-generation benchmark

Run:

```bash
python benchmark.py \
  --url http://localhost:8000 \
  --output-json result.json
```

Defaults use closed-loop concurrency 16, 32 measured requests, approximately
1,024 input tokens, and 4,096 to 8,192 output tokens. The generated prompt set
is deterministic and explicitly identified as VibeSys tuning data, not the
sealed PIQ evaluation cohort.
