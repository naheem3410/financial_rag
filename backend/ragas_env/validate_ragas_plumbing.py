"""Standalone, no-network validation of score_with_ragas.py's plumbing.
Not part of the main pytest suite (different interpreter, real ragas
dependency) -- run directly with this venv's own python:

    ragas_env/Scripts/python.exe  ragas_env/validate_ragas_plumbing.py

Monkeypatches score_with_ragas._build_llm_and_embeddings() to return a
fake LLM and a fake embedder (no OpenAI client, no network, no API key
needed) that return fixed canned responses, then runs the real
score_with_ragas.main() end to end against a throwaway input file and
checks the output file it writes has the shape ragas_bridge.py expects.

This proves the config/wiring (evaluate() call, metric binding, column
mapping, output serialization) is correct. It does NOT prove the real
RAGAS faithfulness/answer_relevancy numbers come out sensible against a
real judge model -- only a real, paid smoke test (reserved for the
owner's own go-ahead) closes that gap.
"""
from __future__ import annotations

import json
import sys
import tempfile
import warnings
from pathlib import Path

warnings.filterwarnings("ignore", category=DeprecationWarning)

sys.path.insert(0, str(Path(__file__).resolve().parent))
import score_with_ragas  # noqa: E402

FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}" + (f" -- {detail}" if detail and not condition else ""))
    if not condition:
        FAILURES.append(name)


def _build_fake_llm_and_embeddings():
    from langchain_core.outputs import Generation
    from ragas.llms.base import BaseRagasLLM, LLMResult
    from ragas.embeddings.base import BaseRagasEmbeddings
    from ragas.run_config import RunConfig

    canned = {
        "Break down each sentence": '{"statements": ["Revenue was 10 billion dollars."]}',
        "judge the faithfulness": (
            '{"statements": [{"statement": "Revenue was 10 billion dollars.", '
            '"reason": "directly stated in the context", "verdict": 1}]}'
        ),
        "Generate a question for the given answer": '{"question": "What was revenue?", "noncommittal": 0}',
    }

    def canned_for(prompt_str: str) -> str:
        for key, value in canned.items():
            if key in prompt_str:
                return value
        raise AssertionError(f"validate_ragas_plumbing: no canned response for prompt:\n{prompt_str[:300]}")

    fast_fail = RunConfig(max_retries=0, timeout=10, max_wait=1)

    class FakeLLM(BaseRagasLLM):
        def generate_text(self, prompt, n=1, temperature=0.01, stop=None, callbacks=None) -> LLMResult:
            text = canned_for(prompt.to_string())
            return LLMResult(generations=[[Generation(text=text)] * n])

        async def agenerate_text(self, prompt, n=1, temperature=0.01, stop=None, callbacks=None) -> LLMResult:
            return self.generate_text(prompt, n=n, temperature=temperature, stop=stop, callbacks=callbacks)

        def is_finished(self, response) -> bool:
            return True

    class FakeEmbeddings(BaseRagasEmbeddings):
        def embed_query(self, text):
            return [0.1, 0.2, 0.3]

        def embed_documents(self, texts):
            return [[0.1, 0.2, 0.3] for _ in texts]

        async def aembed_query(self, text):
            return self.embed_query(text)

        async def aembed_documents(self, texts):
            return self.embed_documents(texts)

    return FakeLLM(run_config=fast_fail), FakeEmbeddings()


def main() -> int:
    score_with_ragas._build_llm_and_embeddings = _build_fake_llm_and_embeddings

    sample_input = {
        "schema_version": 1,
        "items": [
            {
                "collision_id": "val-1",
                "question": "What was revenue?",
                "answer": "Revenue was 10 billion dollars.",
                "contexts": ["Total revenue for the period was 10 billion dollars."],
                "reference": "10 billion dollars",
            },
            {
                "collision_id": "val-2",
                "question": "What was revenue?",
                "answer": "Revenue was 10 billion dollars.",
                "contexts": ["Total revenue for the period was 10 billion dollars."],
                "reference": "",
            },
        ],
    }

    with tempfile.TemporaryDirectory() as tmp:
        input_path = Path(tmp) / "input.json"
        output_path = Path(tmp) / "output.json"
        input_path.write_text(json.dumps(sample_input), encoding="utf-8")

        exit_code = score_with_ragas.main(["score_with_ragas.py", str(input_path), str(output_path)])
        check("main() returns exit code 0", exit_code == 0, f"got {exit_code}")
        check("output file was written", output_path.exists())

        if output_path.exists():
            data = json.loads(output_path.read_text(encoding="utf-8"))
            check("output has 'schema_version'", data.get("schema_version") == 1)
            items = data.get("items", [])
            check("output has 2 scored items", len(items) == 2, f"got {len(items)}")
            if len(items) == 2:
                ids = [it.get("collision_id") for it in items]
                check("collision_ids preserved and in order", ids == ["val-1", "val-2"], f"got {ids}")
                for it in items:
                    check(
                        f"{it.get('collision_id')}: faithfulness is a float in [0,1]",
                        isinstance(it.get("faithfulness"), (int, float)) and 0.0 <= it["faithfulness"] <= 1.0,
                        f"got {it.get('faithfulness')!r}",
                    )
                    check(
                        f"{it.get('collision_id')}: answer_relevancy is a float in [0,1]",
                        isinstance(it.get("answer_relevancy"), (int, float)) and 0.0 <= it["answer_relevancy"] <= 1.0,
                        f"got {it.get('answer_relevancy')!r}",
                    )

        # malformed-input path: must fail loudly, not silently write a bad file
        bad_input_path = Path(tmp) / "bad_input.json"
        bad_output_path = Path(tmp) / "bad_output.json"
        bad_input_path.write_text(json.dumps({"schema_version": 1, "items": [{"collision_id": "x"}]}), encoding="utf-8")
        raised = False
        try:
            score_with_ragas.main(["score_with_ragas.py", str(bad_input_path), str(bad_output_path)])
        except ValueError:
            raised = True
        check("malformed input raises ValueError instead of writing a bad output file", raised)
        check("no output file written for malformed input", not bad_output_path.exists())

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) FAILED: {FAILURES}")
        return 1
    print("All plumbing checks passed (no real network calls were made).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
