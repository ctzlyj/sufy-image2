# Agent Workflow and Key Safety

## Preferred Invocation

If the host can set a process-local environment variable without printing it, set `LTS4AI_API_KEY` only for the child process and run the CLI.

If not, open an interactive process with `--api-key-stdin`:

```bash
python scripts/sufy_image2.py generate --api-key-stdin --prompt "..."
```

Then send the key as one stdin line through the Agent's process-input tool. The script reads the line without writing it to stdout.

## Never Do This

- Never use `--api-key <value>`; that option intentionally does not exist.
- Never include a key in a shell command, URL, filename, `.env`, script, README, log, commit, issue, or final answer.
- Never echo the key back to the user after receiving it.
- Never attach the LTS4AI Authorization header when downloading a provider-returned remote image URL.

## Choosing a Command

| User intent | Command |
|---|---|
| Create from text | `generate` |
| Modify using references | `edit` |
| Several variations | `batch --prompt ... --count N` |
| Different prompt per output | `batch --prompts-file prompts.txt` |
| Check model visibility | `models` |

For edits, preserve reference order. Put the main product or identity reference first because Adaptive ratio uses the first measurable image.

## Output Handling

The CLI prints JSON containing absolute paths, MIME types, byte sizes, model, and resolved size. Display or render each path in capable hosts. Do not embed the API key or source-image bytes in the response.

## Errors

- HTTP 401/403: ask the user to verify the LTS4AI key and model permission.
- HTTP 429: explain that rate limit or quota was reached; avoid immediate high-concurrency retry loops.
- HTTP 5xx: the CLI already performs bounded retries; suggest retrying later if all attempts fail.
- Missing image data: preserve the provider status and ask for a retry; do not invent an output.
- Unsupported reference: convert AVIF/HEIC or other formats to PNG/JPEG before retrying.
- More than 15 MB: reduce image count or compress references without changing the product identity.

