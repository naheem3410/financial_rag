# ragas_env -- isolated RAGAS-scoring environment

## Why this exists

`ragas` cannot be installed into the main project environment: its
`datasets` dependency drags `huggingface-hub` to a version `transformers`
(and therefore `sentence-transformers`, the real embedder/reranker this
project depends on) refuses. Confirmed and reverted once already --
see `backend/KNOWN_ISSUES.md`, decision 20. This directory is a second,
throwaway-style venv that holds the real `ragas` package and nothing that
conflicts with the main environment. The two sides talk only through
plain JSON files on disk, never a shared Python import.

## What's here

- `requirements.txt` -- pinned, `pip check`-clean dependency list. No
  `sentence-transformers` / `transformers` anywhere in it.
- `score_with_ragas.py` -- the script that actually calls `ragas.evaluate()`.
  Reads an input JSON file, writes an output JSON file. Run under *this*
  venv's own interpreter, never the main project's.
- `validate_ragas_plumbing.py` -- standalone (non-pytest) check that runs
  `score_with_ragas.py`'s full pipeline against a fake LLM and a fake
  embedder (no network, no API key, no spend) and checks the output file's
  shape. Not part of the main test suite -- different interpreter.

## Setting it up

```
cd backend
python -m venv ragas_env
ragas_env/Scripts/python.exe -m pip install -r ragas_env/requirements.txt   # Windows
ragas_env/bin/python -m pip install -r ragas_env/requirements.txt           # POSIX
```

## Running a real scoring pass

Needs a real `OPENAI_API_KEY` in the environment (already set on this
machine per the project's usual convention). Costs real money -- every
call goes through `gpt-4.1-mini-2025-04-14` (the judge) and
`text-embedding-3-small` (for `answer_relevancy`'s embeddings), both via
the real OpenAI API.

```
ragas_env/Scripts/python.exe  ragas_env/score_with_ragas.py  <input.json>  <output.json>
```

From the main environment, the normal way to call this is through
`backend/evaluation/ragas_bridge.py`'s `run_ragas_scoring()`, which shells
out to `ragas_env/Scripts/python.exe` (or `ragas_env/bin/python` on POSIX)
by its literal path and passes the two file paths as positional arguments.
It never imports `ragas` itself, and `ragas_bridge.py` is never imported by
anything running under this venv.

## Validating without spending money

```
ragas_env/Scripts/python.exe  ragas_env/validate_ragas_plumbing.py
```

This proves the wiring (metric binding, column mapping, `evaluate()` call,
output JSON shape) is correct using a fake LLM/embedder that returns fixed
canned responses. It does **not** prove the real RAGAS numbers come out
sensible against the real judge model on real retrieved contexts -- that
needs one real, paid smoke test, which is reserved for the owner's own
go-ahead and was deliberately not run as part of building this plumbing.

## A note on the RAGAS API used here

`ragas` 0.4.4 ships two metric APIs:

- the newer `ragas.metrics.collections.Faithfulness` / `AnswerRelevancy`
  classes, built for the `instructor` library and a direct OpenAI client
  (no langchain needed) -- but `ragas.evaluate()` in this version flatly
  rejects them (`"All metrics must be initialised metric objects"`).
- the older `ragas.metrics.faithfulness` / `answer_relevancy` singleton
  objects, which `evaluate()` does accept, bound to a `LangchainLLMWrapper`
  around `langchain_openai.ChatOpenAI` and a `LangchainEmbeddingsWrapper`
  around `langchain_openai.OpenAIEmbeddings`.

`score_with_ragas.py` uses the second (legacy) path, since the task is
"call `ragas.evaluate()`", not "use the newest internal API". `langchain`
and `langchain-openai` are already transitive dependencies of `ragas`
0.4.4 itself in this venv (see `requirements.txt`) -- nothing extra was
added to get this working, and critically `langchain_openai.OpenAIEmbeddings`
calls the real OpenAI embeddings API, not a local model, so this never
reintroduces the `huggingface-hub`/`transformers` conflict this venv
exists to avoid.
