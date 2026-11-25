"""Walkthrough of one document being ingested, from PDF file to searchable facts.
Samples come from a real run on the first 7 pages of one filing. Long lists show two items and a count.
"""
from __future__ import annotations

import inspect
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from contracts import numeric
from llm import routing
from offline import corpus, full_build
from offline.embedding import bge_embedder
from offline.extraction import numeric_extractor
from offline.indexing import build as index_build
from offline.indexing import dense_index_faiss, metadata_store_postgres
from offline.metadata import tagger
from offline.parsers import filing_date, layout_parser, page_filter
from offline.propositions import segmenter_llm
from offline.registry import placeholder_builder

REPO = Path(__file__).resolve().parents[2]
DOCUMENT = '3M_2023Q2_10Q'


@dataclass
class Step:
    number: str
    function: Callable | None
    does: str
    request: Any
    response: Any
    seconds: float | None = None
    times_called: int = 1
    model_call: dict | None = None
    location: str = ""

    def where(self) -> str:
        if self.function is None:
            return self.location
        path = inspect.getsourcefile(self.function) or ""
        line = inspect.getsourcelines(self.function)[1]
        return f"{Path(path).resolve().relative_to(REPO).as_posix()}:{line}"


def walk(steps: list[Step], detail: bool = False) -> None:
    for step in steps:
        pad = "    " * step.number.count(".")
        name = getattr(step.function, "__qualname__", None) or step.location.split("/")[-1]
        extra = f", called {step.times_called} times" if step.times_called > 1 else ""
        took = f", {step.seconds}s" if step.seconds else ""
        print(f"{pad}{step.number}  {name}  ->  {step.where()}{took}{extra}")
        print(f"{pad}      {step.does}")
        if detail:
            print(f"{pad}      request:  {step.request}")
            print(f"{pad}      response: {step.response}")


FLOW = [
    Step(
        number='1',
        function=corpus.resolve_pdf_paths,
        does='Finds the PDF file for each document name and stops early if any is missing.',
        request={'doc_names': ['3M_2023Q2_10Q'], 'pdf_dir': 'data/pdfs'},
        response={'3M_2023Q2_10Q': 'data/pdfs/3M_2023Q2_10Q.pdf'},
    ),
    Step(
        number='2',
        function=full_build.run_full_build,
        does='Runs the whole ingestion for a list of documents and returns a report of what it built.',
        request={'doc_names': ['3M_2023Q2_10Q'],
                 'config': {'experiment_id': 'c1_baseline',
                            'enable_npce': False,
                            'enable_cove': False,
                            'retrieval': {'mode': 'hybrid',
                                          'top_k': 20,
                                          'rerank_top_k': 10,
                                          'enable_reranker': True,
                                          'time_weighting': True},
                            'indexing': {'granularity': 'proposition'},
                            'llm': {'defaults': {'temperature': 0.0, 'structured_output': True},
                                    'call_sites': {'proposition_segmenter': {'provider': 'openai',
                                                                             'model': 'gpt-4.1-mini-2025-04-14',
                                                                             'temperature': 0.0,
                                                                             'seed': None,
                                                                             'concurrency': 16,
                                                                             'include_reasoning_field': False},
                                                   'proposition_segmenter_escalation': {'provider': 'openai',
                                                                                        'model': 'gpt-5.6-luna',
                                                                                        'temperature': 0.0,
                                                                                        'seed': None,
                                                                                        'concurrency': 1,
                                                                                        'include_reasoning_field': True},
                                                   'numeric_qualifier_resolver': {'provider': 'openai',
                                                                                  'model': '<pinned-version>',
                                                                                  'temperature': 0.0,
                                                                                  'seed': None,
                                                                                  'concurrency': 1,
                                                                                  'include_reasoning_field': True},
                                                   'query_splitter': {'provider': 'openai',
                                                                      'model': '<pinned-version>',
                                                                      'temperature': 0.0,
                                                                      'seed': None,
                                                                      'concurrency': 1,
                                                                      'include_reasoning_field': True},
                                                   'fff_rewriter': {'provider': 'openai',
                                                                    'model': 'gpt-4.1-mini-2025-04-14',
                                                                    'temperature': 0.0,
                                                                    'seed': None,
                                                                    'concurrency': 1,
                                                                    'include_reasoning_field': True},
                                                   'generator': {'provider': 'openai',
                                                                 'model': 'gpt-4.1-mini-2025-04-14',
                                                                 'temperature': 0.0,
                                                                 'seed': None,
                                                                 'concurrency': 1,
                                                                 'include_reasoning_field': True},
                                                   'cove_verification': {'provider': 'openai',
                                                                         'model': 'gpt-4.1-mini-2025-04-14',
                                                                         'temperature': 0.0,
                                                                         'seed': None,
                                                                         'concurrency': 1,
                                                                         'include_reasoning_field': True}},
                                    'providers': {'openai': {'api_key_env': 'OPENAI_API_KEY',
                                                             'base_url': None,
                                                             'rpm': 500,
                                                             'tpm': 200000,
                                                             'rpd': 10000,
                                                             'seed': None},
                                                  'anthropic': {'api_key_env': 'ANTHROPIC_API_KEY',
                                                                'base_url': None,
                                                                'rpm': None,
                                                                'tpm': None,
                                                                'rpd': None,
                                                                'seed': None},
                                                  'ollama': {'api_key_env': None,
                                                             'base_url': 'http://localhost:11434',
                                                             'rpm': None,
                                                             'tpm': None,
                                                             'rpd': None,
                                                             'seed': 42}}},
                            'embedding': {'model': 'BAAI/bge-base-en-v1.5',
                                          'revision': 'a5beb1e3e68b9ab74eb54cfd186867f64f240e1a',
                                          'dimension': 768},
                            'corpus': {'pdf_dir': 'data/pdfs', 'required_docs_file': 'data/required_docs.txt'},
                            'guards': {'numeric_verbatim': True, 'deterministic_numeric_comparison': True}},
                 'pdf_paths': {'3M_2023Q2_10Q': '<WindowsPath>'},
                 'company_names': {'3M_2023Q2_10Q': '3M Company'},
                 'checkpoint_path': 'C:\\Users\\NAHEEM~1.QUA\\AppData\\Local\\Temp\\walk_syl9yun7\\checkpoint.json',
                 'max_spend_usd': 1.0,
                 'embedder': '<BGEEmbedder>',
                 'dense_index_path': 'C:\\Users\\NAHEEM~1.QUA\\AppData\\Local\\Temp\\walk_syl9yun7\\dense_index',
                 'postgres_dsn': 'postgresql://financial_rag:financial_rag_dev@localhost:5432/financial_rag_test'},
        response={'config_hash': '',
                  'index_build': {'build_id': '7fc1e3d61d59e3f8',
                                  'proposition_count': 271,
                                  'embedder_model_name': 'BAAI/bge-base-en-v1.5',
                                  'embedder_revision': 'a5beb1e3e68b9ab74eb54cfd186867f64f240e1a',
                                  'embedder_dimension': 768,
                                  'indexed_doc_names': ['3M_2023Q2_10Q'],
                                  'skipped_pages_by_reason': {'cover': 1, 'table_of_contents': 1},
                                  'gold_overlap_clean': None},
                  'llm_call_sites': [{'call_site': 'proposition_segmenter',
                                      'provider': 'openai',
                                      'model': 'gpt-4.1-mini-2025-04-14',
                                      'temperature': 0.0,
                                      'seed': None,
                                      'seed_supported': False,
                                      'include_reasoning_field': False,
                                      'adapter_version': '1.0.0',
                                      'temperature_supported': True}],
                  'total_requests': 5,
                  'total_input_tokens': 13370,
                  'total_output_tokens': 63641,
                  'total_cost_usd': 0.13505,
                  'effective_concurrency': 0.78449},
        seconds=447.096,
    ),
    Step(
        number='2.1',
        function=layout_parser.parse_document,
        does='Reads the PDF into pages of text and tables.',
        request={'path': '<WindowsPath>'},
        response={'pages': [{'page_number': 0,
                             'text': 'Table of Contents\n'
                                     'UNITED STATES\n'
                                     'SECURITIES AND EXCHANGE COMMISSION\n'
                                     'WASHINGTON, D.C. 20549\n'
                                     'FORM 10-Q\n'
                                     '☒ QUARTERLY REPORT PURSUANT TO SECTION 13 OR 15(d) OF THE SECURITIES E... (521 '
                                     'characters)',
                             'tables': []},
                            {'page_number': 1,
                             'text': 'Table of Contents\n'
                                     '3M COMPANY\n'
                                     'Form 10-Q for the Quarterly Period Ended June 30, 2023\n'
                                     'BEGINNING\n'
                                     'TABLE OF CONTENTS PAGE\n'
                                     'PART I FINANCIAL INFORMATION\n'
                                     'ITEM 1. Financial Statem... (521 characters)',
                             'tables': []},
                            '... 5 more, 7 in total'],
                  'sections': [{'title': 'Table of Contents', 'start_offset': 0},
                               {'title': 'UNITED STATES', 'start_offset': 18},
                               '... 484 more, 486 in total'],
                  'source_format': 'pdf'},
        seconds=21.968,
    ),
    Step(
        number='2.1.1',
        function=layout_parser._parse_pdf,
        does='The PDF reader behind parse_document.',
        request={'path': '<WindowsPath>'},
        response={'pages': [{'page_number': 0,
                             'text': 'Table of Contents\n'
                                     'UNITED STATES\n'
                                     'SECURITIES AND EXCHANGE COMMISSION\n'
                                     'WASHINGTON, D.C. 20549\n'
                                     'FORM 10-Q\n'
                                     '☒ QUARTERLY REPORT PURSUANT TO SECTION 13 OR 15(d) OF THE SECURITIES E... (521 '
                                     'characters)',
                             'tables': []},
                            {'page_number': 1,
                             'text': 'Table of Contents\n'
                                     '3M COMPANY\n'
                                     'Form 10-Q for the Quarterly Period Ended June 30, 2023\n'
                                     'BEGINNING\n'
                                     'TABLE OF CONTENTS PAGE\n'
                                     'PART I FINANCIAL INFORMATION\n'
                                     'ITEM 1. Financial Statem... (521 characters)',
                             'tables': []},
                            '... 90 more, 92 in total'],
                  'sections': [{'title': 'Table of Contents', 'start_offset': 0},
                               {'title': 'UNITED STATES', 'start_offset': 18},
                               '... 484 more, 486 in total'],
                  'source_format': 'pdf'},
        seconds=21.968,
    ),
    Step(
        number='2.1.1.1',
        function=layout_parser._find_section_boundaries,
        does='Marks where each section starts in the text.',
        request={'text': 'Table of Contents\n'
                         'UNITED STATES\n'
                         'SECURITIES AND EXCHANGE COMMISSION\n'
                         'WASHINGTON, D.C. 20549\n'
                         'FORM 10-Q\n'
                         '☒ QUARTERLY REPORT PURSUANT TO SECTION 13 OR 15(d) OF THE SECURITIES E... (523 characters)'},
        response=[{'title': 'Table of Contents', 'start_offset': 0},
                  {'title': 'UNITED STATES', 'start_offset': 18},
                  '... 484 more, 486 in total'],
        seconds=0.003,
    ),
    Step(
        number='2.2',
        function=page_filter.filter_pages,
        does='Drops pages that carry no facts, and records why.',
        request={'pages': [{'page_number': 0,
                            'text': 'Table of Contents\n'
                                    'UNITED STATES\n'
                                    'SECURITIES AND EXCHANGE COMMISSION\n'
                                    'WASHINGTON, D.C. 20549\n'
                                    'FORM 10-Q\n'
                                    '☒ QUARTERLY REPORT PURSUANT TO SECTION 13 OR 15(d) OF THE SECURITIES E... (521 '
                                    'characters)',
                            'tables': []},
                           {'page_number': 1,
                            'text': 'Table of Contents\n'
                                    '3M COMPANY\n'
                                    'Form 10-Q for the Quarterly Period Ended June 30, 2023\n'
                                    'BEGINNING\n'
                                    'TABLE OF CONTENTS PAGE\n'
                                    'PART I FINANCIAL INFORMATION\n'
                                    'ITEM 1. Financial Statem... (521 characters)',
                            'tables': []},
                           '... 5 more, 7 in total']},
        response=[[{'page_number': 2,
                    'text': 'Table of Contents\n'
                            '3M COMPANY\n'
                            'FORM 10-Q\n'
                            'For the Quarterly Period Ended June 30, 2023\n'
                            'PART I. Financial Information\n'
                            'Item 1. Financial Statements.\n'
                            '3M Company and Subsidiarie... (521 characters)',
                    'tables': [[['Net sales', '', '... 11 more, 13 in total'],
                                ['', '', '... 11 more, 13 in total'],
                                '... 25 more, 27 in total']]},
                   {'page_number': 3,
                    'text': 'Table of Contents\n'
                            '3M Company and Subsidiaries\n'
                            'Consolidated Statement of Comprehensive Income (Loss)\n'
                            '(Unaudited)\n'
                            'Three months ended Six months ended\n'
                            'June 30, June 30,\n'
                            '(Mil... (520 characters)',
                    'tables': [[['Net income (loss) including noncontrolling interest',
                                 '',
                                 '... 11 more, 13 in total'],
                                ['Other comprehensive income (loss), net of tax:', '', '... 11 more, 13 in total'],
                                '... 7 more, 9 in total']]},
                   '... 3 more, 5 in total'],
                  {'0': 'cover', '1': 'table_of_contents'}],
        seconds=0.003,
    ),
    Step(
        number='2.2.1',
        function=page_filter.classify_page,
        does='Decides whether one page should be skipped.',
        request={'page': {'page_number': 0,
                          'text': 'Table of Contents\n'
                                  'UNITED STATES\n'
                                  'SECURITIES AND EXCHANGE COMMISSION\n'
                                  'WASHINGTON, D.C. 20549\n'
                                  'FORM 10-Q\n'
                                  '☒ QUARTERLY REPORT PURSUANT TO SECTION 13 OR 15(d) OF THE SECURITIES E... (521 '
                                  'characters)',
                          'tables': []}},
        response='cover',
        seconds=0.0,
        times_called=7,
    ),
    Step(
        number='2.3',
        function=full_build._document_context_for,
        does='Collects the company, filing type and filing date for the document.',
        request={'doc_name': '3M_2023Q2_10Q',
                 'pages': [{'page_number': 0,
                            'text': 'Table of Contents\n'
                                    'UNITED STATES\n'
                                    'SECURITIES AND EXCHANGE COMMISSION\n'
                                    'WASHINGTON, D.C. 20549\n'
                                    'FORM 10-Q\n'
                                    '☒ QUARTERLY REPORT PURSUANT TO SECTION 13 OR 15(d) OF THE SECURITIES E... (521 '
                                    'characters)',
                            'tables': []},
                           {'page_number': 1,
                            'text': 'Table of Contents\n'
                                    '3M COMPANY\n'
                                    'Form 10-Q for the Quarterly Period Ended June 30, 2023\n'
                                    'BEGINNING\n'
                                    'TABLE OF CONTENTS PAGE\n'
                                    'PART I FINANCIAL INFORMATION\n'
                                    'ITEM 1. Financial Statem... (521 characters)',
                            'tables': []},
                           '... 5 more, 7 in total'],
                 'company_names': {'3M_2023Q2_10Q': '3M Company'}},
        response={'company': '3M Company',
                  'filing_type': '10-Q',
                  'filing_date': '2023-06-30',
                  'source_uri': '3M_2023Q2_10Q',
                  'fiscal_year': 2023,
                  'quarter': 2},
        seconds=0.0,
    ),
    Step(
        number='2.3.1',
        function=corpus.parse_doc_name,
        does='Reads the year, filing type and quarter from the file name.',
        request={'doc_name': '3M_2023Q2_10Q'},
        response={'fiscal_year': 2023, 'filing_type': '10-Q', 'quarter': 2},
        seconds=0.0,
    ),
    Step(
        number='2.3.2',
        function=filing_date.extract_filing_period_end,
        does='Finds the period end date printed on the first pages.',
        request={'pages': [{'page_number': 0,
                            'text': 'Table of Contents\n'
                                    'UNITED STATES\n'
                                    'SECURITIES AND EXCHANGE COMMISSION\n'
                                    'WASHINGTON, D.C. 20549\n'
                                    'FORM 10-Q\n'
                                    '☒ QUARTERLY REPORT PURSUANT TO SECTION 13 OR 15(d) OF THE SECURITIES E... (521 '
                                    'characters)',
                            'tables': []},
                           {'page_number': 1,
                            'text': 'Table of Contents\n'
                                    '3M COMPANY\n'
                                    'Form 10-Q for the Quarterly Period Ended June 30, 2023\n'
                                    'BEGINNING\n'
                                    'TABLE OF CONTENTS PAGE\n'
                                    'PART I FINANCIAL INFORMATION\n'
                                    'ITEM 1. Financial Statem... (521 characters)',
                            'tables': []},
                           '... 5 more, 7 in total']},
        response='2023-06-30',
        seconds=0.0,
    ),
    Step(
        number='2.4',
        function=routing.build_llm_client,
        does='Creates the model client for fact splitting. It runs twice: the main model, then the stronger fallback.',
        request={'call_site_name': 'proposition_segmenter',
                 'config': {'experiment_id': 'c1_baseline',
                            'enable_npce': False,
                            'enable_cove': False,
                            'retrieval': {'mode': 'hybrid',
                                          'top_k': 20,
                                          'rerank_top_k': 10,
                                          'enable_reranker': True,
                                          'time_weighting': True},
                            'indexing': {'granularity': 'proposition'},
                            'llm': {'defaults': {'temperature': 0.0, 'structured_output': True},
                                    'call_sites': {'proposition_segmenter': {'provider': 'openai',
                                                                             'model': 'gpt-4.1-mini-2025-04-14',
                                                                             'temperature': 0.0,
                                                                             'seed': None,
                                                                             'concurrency': 16,
                                                                             'include_reasoning_field': False},
                                                   'proposition_segmenter_escalation': {'provider': 'openai',
                                                                                        'model': 'gpt-5.6-luna',
                                                                                        'temperature': 0.0,
                                                                                        'seed': None,
                                                                                        'concurrency': 1,
                                                                                        'include_reasoning_field': True},
                                                   'numeric_qualifier_resolver': {'provider': 'openai',
                                                                                  'model': '<pinned-version>',
                                                                                  'temperature': 0.0,
                                                                                  'seed': None,
                                                                                  'concurrency': 1,
                                                                                  'include_reasoning_field': True},
                                                   'query_splitter': {'provider': 'openai',
                                                                      'model': '<pinned-version>',
                                                                      'temperature': 0.0,
                                                                      'seed': None,
                                                                      'concurrency': 1,
                                                                      'include_reasoning_field': True},
                                                   'fff_rewriter': {'provider': 'openai',
                                                                    'model': 'gpt-4.1-mini-2025-04-14',
                                                                    'temperature': 0.0,
                                                                    'seed': None,
                                                                    'concurrency': 1,
                                                                    'include_reasoning_field': True},
                                                   'generator': {'provider': 'openai',
                                                                 'model': 'gpt-4.1-mini-2025-04-14',
                                                                 'temperature': 0.0,
                                                                 'seed': None,
                                                                 'concurrency': 1,
                                                                 'include_reasoning_field': True},
                                                   'cove_verification': {'provider': 'openai',
                                                                         'model': 'gpt-4.1-mini-2025-04-14',
                                                                         'temperature': 0.0,
                                                                         'seed': None,
                                                                         'concurrency': 1,
                                                                         'include_reasoning_field': True}},
                                    'providers': {'openai': {'api_key_env': 'OPENAI_API_KEY',
                                                             'base_url': None,
                                                             'rpm': 500,
                                                             'tpm': 200000,
                                                             'rpd': 10000,
                                                             'seed': None},
                                                  'anthropic': {'api_key_env': 'ANTHROPIC_API_KEY',
                                                                'base_url': None,
                                                                'rpm': None,
                                                                'tpm': None,
                                                                'rpd': None,
                                                                'seed': None},
                                                  'ollama': {'api_key_env': None,
                                                             'base_url': 'http://localhost:11434',
                                                             'rpm': None,
                                                             'tpm': None,
                                                             'rpd': None,
                                                             'seed': 42}}},
                            'embedding': {'model': 'BAAI/bge-base-en-v1.5',
                                          'revision': 'a5beb1e3e68b9ab74eb54cfd186867f64f240e1a',
                                          'dimension': 768},
                            'corpus': {'pdf_dir': 'data/pdfs', 'required_docs_file': 'data/required_docs.txt'},
                            'guards': {'numeric_verbatim': True, 'deterministic_numeric_comparison': True}}},
        response='<RoutedLLMClient>',
        seconds=1.685,
        times_called=2,
    ),
    Step(
        number='2.5',
        function=segmenter_llm.segment_pages,
        does='Splits the pages into short facts, two pages at a time.',
        request={'pages': [{'page_number': 2,
                            'text': 'Table of Contents\n'
                                    '3M COMPANY\n'
                                    'FORM 10-Q\n'
                                    'For the Quarterly Period Ended June 30, 2023\n'
                                    'PART I. Financial Information\n'
                                    'Item 1. Financial Statements.\n'
                                    '3M Company and Subsidiarie... (521 characters)',
                            'tables': [[['Net sales', '', '... 11 more, 13 in total'],
                                        ['', '', '... 11 more, 13 in total'],
                                        '... 25 more, 27 in total']]},
                           {'page_number': 3,
                            'text': 'Table of Contents\n'
                                    '3M Company and Subsidiaries\n'
                                    'Consolidated Statement of Comprehensive Income (Loss)\n'
                                    '(Unaudited)\n'
                                    'Three months ended Six months ended\n'
                                    'June 30, June 30,\n'
                                    '(Mil... (520 characters)',
                            'tables': [[['Net income (loss) including noncontrolling interest',
                                         '',
                                         '... 11 more, 13 in total'],
                                        ['Other comprehensive income (loss), net of tax:',
                                         '',
                                         '... 11 more, 13 in total'],
                                        '... 7 more, 9 in total']]},
                           '... 3 more, 5 in total'],
                 'document_context': {'company': '3M Company',
                                      'filing_type': '10-Q',
                                      'filing_date': '2023-06-30',
                                      'source_uri': '3M_2023Q2_10Q',
                                      'fiscal_year': 2023,
                                      'quarter': 2},
                 'llm_client': '<RoutedLLMClient>',
                 'batch_size': 2,
                 'max_consecutive_batch_failures': 10,
                 'max_workers': 1,
                 'escalation_llm_client': '<RoutedLLMClient>',
                 'on_batch_start': '<function>',
                 'on_batch_complete': '<function>',
                 'completed_batches': {}},
        response=[{'text': '3M Company had net sales of $8,325 million for the three months ended June 30, 2023.',
                   'source_offset_hint': '--- PAGE 2 --- Table of Contents 3M COMPANY FORM 10-Q For the Quarterly '
                                         'Period Ended June 30, 2023 PART I. Financial Information Item 1. Financial '
                                         'Statements. 3M Company ... (289 characters)',
                   'page_number': 2},
                  {'text': '3M Company had net sales of $8,702 million for the three months ended June 30, 2022.',
                   'source_offset_hint': '--- PAGE 2 --- Table of Contents 3M COMPANY FORM 10-Q For the Quarterly '
                                         'Period Ended June 30, 2023 PART I. Financial Information Item 1. Financial '
                                         'Statements. 3M Company ... (289 characters)',
                   'page_number': 2},
                  '... 269 more, 271 in total'],
        seconds=397.367,
    ),
    Step(
        number='2.5.1',
        function=segmenter_llm._make_batches,
        does='Groups the pages into batches of two.',
        request={'pages': [{'page_number': 2,
                            'text': 'Table of Contents\n'
                                    '3M COMPANY\n'
                                    'FORM 10-Q\n'
                                    'For the Quarterly Period Ended June 30, 2023\n'
                                    'PART I. Financial Information\n'
                                    'Item 1. Financial Statements.\n'
                                    '3M Company and Subsidiarie... (521 characters)',
                            'tables': [[['Net sales', '', '... 11 more, 13 in total'],
                                        ['', '', '... 11 more, 13 in total'],
                                        '... 25 more, 27 in total']]},
                           {'page_number': 3,
                            'text': 'Table of Contents\n'
                                    '3M Company and Subsidiaries\n'
                                    'Consolidated Statement of Comprehensive Income (Loss)\n'
                                    '(Unaudited)\n'
                                    'Three months ended Six months ended\n'
                                    'June 30, June 30,\n'
                                    '(Mil... (520 characters)',
                            'tables': [[['Net income (loss) including noncontrolling interest',
                                         '',
                                         '... 11 more, 13 in total'],
                                        ['Other comprehensive income (loss), net of tax:',
                                         '',
                                         '... 11 more, 13 in total'],
                                        '... 7 more, 9 in total']]},
                           '... 3 more, 5 in total'],
                 'batch_size': 2},
        response=[[{'page_number': 2,
                    'text': 'Table of Contents\n'
                            '3M COMPANY\n'
                            'FORM 10-Q\n'
                            'For the Quarterly Period Ended June 30, 2023\n'
                            'PART I. Financial Information\n'
                            'Item 1. Financial Statements.\n'
                            '3M Company and Subsidiarie... (521 characters)',
                    'tables': [[['Net sales', '', '... 11 more, 13 in total'],
                                ['', '', '... 11 more, 13 in total'],
                                '... 25 more, 27 in total']]},
                   {'page_number': 3,
                    'text': 'Table of Contents\n'
                            '3M Company and Subsidiaries\n'
                            'Consolidated Statement of Comprehensive Income (Loss)\n'
                            '(Unaudited)\n'
                            'Three months ended Six months ended\n'
                            'June 30, June 30,\n'
                            '(Mil... (520 characters)',
                    'tables': [[['Net income (loss) including noncontrolling interest',
                                 '',
                                 '... 11 more, 13 in total'],
                                ['Other comprehensive income (loss), net of tax:', '', '... 11 more, 13 in total'],
                                '... 7 more, 9 in total']]}],
                  [{'page_number': 4,
                    'text': 'Table of Contents\n'
                            '3M Company and Subsidiaries\n'
                            'Consolidated Balance Sheet\n'
                            '(Unaudited)\n'
                            '(Dollars in millions, except per share amount) June 30, 2023 December 31, 2022\n'
                            'Assets... (521 characters)',
                    'tables': [[['Assets', '', '... 5 more, 7 in total'],
                                ['Current assets', '', '... 5 more, 7 in total'],
                                '... 49 more, 51 in total']]},
                   {'page_number': 5,
                    'text': 'Table of Contents\n'
                            '3M Company and Subsidiaries\n'
                            'Consolidated Statement of Cash Flows\n'
                            '(Unaudited)\n'
                            'Six months ended\n'
                            'June 30,\n'
                            '(Millions) 2023 2022\n'
                            'Cash Flows from Operating Ac... (521 characters)',
                    'tables': [[['Cash Flows from Operating Activities', '', '... 5 more, 7 in total'],
                                ['Net income (loss) including noncontrolling interest', '', '... 5 more, 7 in total'],
                                '... 38 more, 40 in total']]}],
                  '... 1 more, 3 in total'],
        seconds=0.0,
    ),
    Step(
        number='2.5.2',
        function=segmenter_llm._segment_batches_sequential,
        does='Works through the batches one after another.',
        request={'batches': [[{'page_number': 2,
                               'text': 'Table of Contents\n'
                                       '3M COMPANY\n'
                                       'FORM 10-Q\n'
                                       'For the Quarterly Period Ended June 30, 2023\n'
                                       'PART I. Financial Information\n'
                                       'Item 1. Financial Statements.\n'
                                       '3M Company and Subsidiarie... (521 characters)',
                               'tables': [[['Net sales', '', '... 11 more, 13 in total'],
                                           ['', '', '... 11 more, 13 in total'],
                                           '... 25 more, 27 in total']]},
                              {'page_number': 3,
                               'text': 'Table of Contents\n'
                                       '3M Company and Subsidiaries\n'
                                       'Consolidated Statement of Comprehensive Income (Loss)\n'
                                       '(Unaudited)\n'
                                       'Three months ended Six months ended\n'
                                       'June 30, June 30,\n'
                                       '(Mil... (520 characters)',
                               'tables': [[['Net income (loss) including noncontrolling interest',
                                            '',
                                            '... 11 more, 13 in total'],
                                           ['Other comprehensive income (loss), net of tax:',
                                            '',
                                            '... 11 more, 13 in total'],
                                           '... 7 more, 9 in total']]}],
                             [{'page_number': 4,
                               'text': 'Table of Contents\n'
                                       '3M Company and Subsidiaries\n'
                                       'Consolidated Balance Sheet\n'
                                       '(Unaudited)\n'
                                       '(Dollars in millions, except per share amount) June 30, 2023 December 31, '
                                       '2022\n'
                                       'Assets... (521 characters)',
                               'tables': [[['Assets', '', '... 5 more, 7 in total'],
                                           ['Current assets', '', '... 5 more, 7 in total'],
                                           '... 49 more, 51 in total']]},
                              {'page_number': 5,
                               'text': 'Table of Contents\n'
                                       '3M Company and Subsidiaries\n'
                                       'Consolidated Statement of Cash Flows\n'
                                       '(Unaudited)\n'
                                       'Six months ended\n'
                                       'June 30,\n'
                                       '(Millions) 2023 2022\n'
                                       'Cash Flows from Operating Ac... (521 characters)',
                               'tables': [[['Cash Flows from Operating Activities', '', '... 5 more, 7 in total'],
                                           ['Net income (loss) including noncontrolling interest',
                                            '',
                                            '... 5 more, 7 in total'],
                                           '... 38 more, 40 in total']]}],
                             '... 1 more, 3 in total'],
                 'document_context': {'company': '3M Company',
                                      'filing_type': '10-Q',
                                      'filing_date': '2023-06-30',
                                      'source_uri': '3M_2023Q2_10Q',
                                      'fiscal_year': 2023,
                                      'quarter': 2},
                 'llm_client': '<RoutedLLMClient>',
                 'max_attempts': 2,
                 'max_consecutive_batch_failures': 10,
                 'escalation_llm_client': '<RoutedLLMClient>',
                 'on_batch_start': '<function>',
                 'on_batch_complete': '<function>',
                 'completed_batches': {}},
        response=[{'text': '3M Company had net sales of $8,325 million for the three months ended June 30, 2023.',
                   'source_offset_hint': '--- PAGE 2 --- Table of Contents 3M COMPANY FORM 10-Q For the Quarterly '
                                         'Period Ended June 30, 2023 PART I. Financial Information Item 1. Financial '
                                         'Statements. 3M Company ... (289 characters)',
                   'page_number': 2},
                  {'text': '3M Company had net sales of $8,702 million for the three months ended June 30, 2022.',
                   'source_offset_hint': '--- PAGE 2 --- Table of Contents 3M COMPANY FORM 10-Q For the Quarterly '
                                         'Period Ended June 30, 2023 PART I. Financial Information Item 1. Financial '
                                         'Statements. 3M Company ... (289 characters)',
                   'page_number': 2},
                  '... 269 more, 271 in total'],
        seconds=397.366,
    ),
    Step(
        number='2.5.2.1',
        function=segmenter_llm._render_page_batch,
        does='Turns a batch of pages into one block of text.',
        request={'pages': [{'page_number': 2,
                            'text': 'Table of Contents\n'
                                    '3M COMPANY\n'
                                    'FORM 10-Q\n'
                                    'For the Quarterly Period Ended June 30, 2023\n'
                                    'PART I. Financial Information\n'
                                    'Item 1. Financial Statements.\n'
                                    '3M Company and Subsidiarie... (521 characters)',
                            'tables': [[['Net sales', '', '... 11 more, 13 in total'],
                                        ['', '', '... 11 more, 13 in total'],
                                        '... 25 more, 27 in total']]},
                           {'page_number': 3,
                            'text': 'Table of Contents\n'
                                    '3M Company and Subsidiaries\n'
                                    'Consolidated Statement of Comprehensive Income (Loss)\n'
                                    '(Unaudited)\n'
                                    'Three months ended Six months ended\n'
                                    'June 30, June 30,\n'
                                    '(Mil... (520 characters)',
                            'tables': [[['Net income (loss) including noncontrolling interest',
                                         '',
                                         '... 11 more, 13 in total'],
                                        ['Other comprehensive income (loss), net of tax:',
                                         '',
                                         '... 11 more, 13 in total'],
                                        '... 7 more, 9 in total']]}]},
        response=('--- PAGE 2 ---\n'
                  'Table of Contents\n'
                  '3M COMPANY\n'
                  'FORM 10-Q\n'
                  'For the Quarterly Period Ended June 30, 2023\n'
                  'PART I. Financial Information\n'
                  'Item 1. Financial Statements.\n'
                  '3M Company ... (521 characters)'),
        seconds=0.0,
        times_called=3,
    ),
    Step(
        number='2.5.2.2',
        function=segmenter_llm._segment_one_batch,
        does='Handles one batch. Runs once per batch, and the first two are shown here.',
        request={'batch': [{'page_number': 2,
                            'text': 'Table of Contents\n'
                                    '3M COMPANY\n'
                                    'FORM 10-Q\n'
                                    'For the Quarterly Period Ended June 30, 2023\n'
                                    'PART I. Financial Information\n'
                                    'Item 1. Financial Statements.\n'
                                    '3M Company and Subsidiarie... (521 characters)',
                            'tables': [[['Net sales', '', '... 11 more, 13 in total'],
                                        ['', '', '... 11 more, 13 in total'],
                                        '... 25 more, 27 in total']]},
                           {'page_number': 3,
                            'text': 'Table of Contents\n'
                                    '3M Company and Subsidiaries\n'
                                    'Consolidated Statement of Comprehensive Income (Loss)\n'
                                    '(Unaudited)\n'
                                    'Three months ended Six months ended\n'
                                    'June 30, June 30,\n'
                                    '(Mil... (520 characters)',
                            'tables': [[['Net income (loss) including noncontrolling interest',
                                         '',
                                         '... 11 more, 13 in total'],
                                        ['Other comprehensive income (loss), net of tax:',
                                         '',
                                         '... 11 more, 13 in total'],
                                        '... 7 more, 9 in total']]}],
                 'document_context': {'company': '3M Company',
                                      'filing_type': '10-Q',
                                      'filing_date': '2023-06-30',
                                      'source_uri': '3M_2023Q2_10Q',
                                      'fiscal_year': 2023,
                                      'quarter': 2},
                 'llm_client': '<RoutedLLMClient>',
                 'max_attempts': 2,
                 'escalation_llm_client': '<RoutedLLMClient>'},
        response=[{'text': '3M Company had net sales of $8,325 million for the three months ended June 30, 2023.',
                   'source_offset_hint': '--- PAGE 2 --- Table of Contents 3M COMPANY FORM 10-Q For the Quarterly '
                                         'Period Ended June 30, 2023 PART I. Financial Information Item 1. Financial '
                                         'Statements. 3M Company ... (289 characters)',
                   'page_number': 2},
                  {'text': '3M Company had net sales of $8,702 million for the three months ended June 30, 2022.',
                   'source_offset_hint': '--- PAGE 2 --- Table of Contents 3M COMPANY FORM 10-Q For the Quarterly '
                                         'Period Ended June 30, 2023 PART I. Financial Information Item 1. Financial '
                                         'Statements. 3M Company ... (289 characters)',
                   'page_number': 2},
                  '... 79 more, 81 in total'],
        seconds=47.091,
        times_called=3,
    ),
    Step(
        number='2.5.2.2.1',
        function=segmenter_llm._render_page_batch,
        does='Turns a batch of pages into one block of text.',
        request={'pages': [{'page_number': 2,
                            'text': 'Table of Contents\n'
                                    '3M COMPANY\n'
                                    'FORM 10-Q\n'
                                    'For the Quarterly Period Ended June 30, 2023\n'
                                    'PART I. Financial Information\n'
                                    'Item 1. Financial Statements.\n'
                                    '3M Company and Subsidiarie... (521 characters)',
                            'tables': [[['Net sales', '', '... 11 more, 13 in total'],
                                        ['', '', '... 11 more, 13 in total'],
                                        '... 25 more, 27 in total']]},
                           {'page_number': 3,
                            'text': 'Table of Contents\n'
                                    '3M Company and Subsidiaries\n'
                                    'Consolidated Statement of Comprehensive Income (Loss)\n'
                                    '(Unaudited)\n'
                                    'Three months ended Six months ended\n'
                                    'June 30, June 30,\n'
                                    '(Mil... (520 characters)',
                            'tables': [[['Net income (loss) including noncontrolling interest',
                                         '',
                                         '... 11 more, 13 in total'],
                                        ['Other comprehensive income (loss), net of tax:',
                                         '',
                                         '... 11 more, 13 in total'],
                                        '... 7 more, 9 in total']]}]},
        response=('--- PAGE 2 ---\n'
                  'Table of Contents\n'
                  '3M COMPANY\n'
                  'FORM 10-Q\n'
                  'For the Quarterly Period Ended June 30, 2023\n'
                  'PART I. Financial Information\n'
                  'Item 1. Financial Statements.\n'
                  '3M Company ... (521 characters)'),
        seconds=0.0,
    ),
    Step(
        number='2.5.2.2.2',
        function=segmenter_llm.segment_propositions,
        does='Asks the model for facts, then drops any fact whose digits are not in the source text.',
        request={'source_span': '--- PAGE 2 ---\n'
                                'Table of Contents\n'
                                '3M COMPANY\n'
                                'FORM 10-Q\n'
                                'For the Quarterly Period Ended June 30, 2023\n'
                                'PART I. Financial Information\n'
                                'Item 1. Financial Statements.\n'
                                '3M Company ... (521 characters)',
                 'document_context': {'company': '3M Company',
                                      'filing_type': '10-Q',
                                      'filing_date': '2023-06-30',
                                      'source_uri': '3M_2023Q2_10Q',
                                      'fiscal_year': 2023,
                                      'quarter': 2},
                 'llm_client': '<RoutedLLMClient>',
                 'max_attempts': 2},
        response=[{'text': '3M Company had net sales of $8,325 million for the three months ended June 30, 2023.',
                   'source_offset_hint': '--- PAGE 2 --- Table of Contents 3M COMPANY FORM 10-Q For the Quarterly '
                                         'Period Ended June 30, 2023 PART I. Financial Information Item 1. Financial '
                                         'Statements. 3M Company ... (289 characters)',
                   'page_number': None},
                  {'text': '3M Company had net sales of $8,702 million for the three months ended June 30, 2022.',
                   'source_offset_hint': '--- PAGE 2 --- Table of Contents 3M COMPANY FORM 10-Q For the Quarterly '
                                         'Period Ended June 30, 2023 PART I. Financial Information Item 1. Financial '
                                         'Statements. 3M Company ... (289 characters)',
                   'page_number': None},
                  '... 79 more, 81 in total'],
        seconds=47.074,
    ),
    Step(
        number='2.5.2.2.2.1',
        function=routing.RoutedLLMClient.complete,
        does='Sends one request to the language model and returns its reply, checked against a fixed shape.',
        request={'payload': {'schema_version': '1.0',
                             'system_prompt': 'You split financial document text into atomic propositions.\n'
                                              '\n'
                                              'An atomic proposition is a single self-contained factual statement '
                                              'that concerns\n'
                                              'exactly one entity, one metr... (521 characters)',
                             'source_span': '--- PAGE 2 ---\n'
                                            'Table of Contents\n'
                                            '3M COMPANY\n'
                                            'FORM 10-Q\n'
                                            'For the Quarterly Period Ended June 30, 2023\n'
                                            'PART I. Financial Information\n'
                                            'Item 1. Financial Statements.\n'
                                            '3M Company ... (521 characters)',
                             'document_context': {'company': '3M Company',
                                                  'filing_type': '10-Q',
                                                  'filing_date': '2023-06-30',
                                                  'source_uri': '3M_2023Q2_10Q',
                                                  'fiscal_year': 2023,
                                                  'quarter': 2}}},
        response={'propositions': [{'text': '3M Company had net sales of $8,325 million for the three months ended '
                                            'June 30, 2023.',
                                    'source_offset_hint': '--- PAGE 2 --- Table of Contents 3M COMPANY FORM 10-Q For '
                                                          'the Quarterly Period Ended June 30, 2023 PART I. '
                                                          'Financial Information Item 1. Financial Statements. 3M '
                                                          'Company ... (289 characters)'},
                                   {'text': '3M Company had net sales of $8,702 million for the three months ended '
                                            'June 30, 2022.',
                                    'source_offset_hint': '--- PAGE 2 --- Table of Contents 3M COMPANY FORM 10-Q For '
                                                          'the Quarterly Period Ended June 30, 2023 PART I. '
                                                          'Financial Information Item 1. Financial Statements. 3M '
                                                          'Company ... (289 characters)'},
                                   '... 102 more, 104 in total']},
        seconds=46.991,
        model_call={'schema': 'PropositionSegmenterOutputNoReasoning',
                    'model': 'gpt-4.1-mini-2025-04-14',
                    'temperature': 0.0},
    ),
    Step(
        number='2.5.2.2.2.2',
        function=numeric.appears_verbatim,
        does="Checks that a fact's numbers appear word for word in the source text.",
        request={'candidate': '3M Company had net sales of $8,325 million for the three months ended June 30, 2023.',
                 'source_text': '--- PAGE 2 ---\n'
                                'Table of Contents\n'
                                '3M COMPANY\n'
                                'FORM 10-Q\n'
                                'For the Quarterly Period Ended June 30, 2023\n'
                                'PART I. Financial Information\n'
                                'Item 1. Financial Statements.\n'
                                '3M Company ... (521 characters)'},
        response=True,
        seconds=0.003,
        times_called=104,
    ),
    Step(
        number='2.5.2.2.3',
        function=segmenter_llm._table_rows_complete,
        does='Checks that table rows kept their real values. If not, the batch is retried on the stronger model.',
        request={'pages': [{'page_number': 2,
                            'text': 'Table of Contents\n'
                                    '3M COMPANY\n'
                                    'FORM 10-Q\n'
                                    'For the Quarterly Period Ended June 30, 2023\n'
                                    'PART I. Financial Information\n'
                                    'Item 1. Financial Statements.\n'
                                    '3M Company and Subsidiarie... (521 characters)',
                            'tables': [[['Net sales', '', '... 11 more, 13 in total'],
                                        ['', '', '... 11 more, 13 in total'],
                                        '... 25 more, 27 in total']]},
                           {'page_number': 3,
                            'text': 'Table of Contents\n'
                                    '3M Company and Subsidiaries\n'
                                    'Consolidated Statement of Comprehensive Income (Loss)\n'
                                    '(Unaudited)\n'
                                    'Three months ended Six months ended\n'
                                    'June 30, June 30,\n'
                                    '(Mil... (520 characters)',
                            'tables': [[['Net income (loss) including noncontrolling interest',
                                         '',
                                         '... 11 more, 13 in total'],
                                        ['Other comprehensive income (loss), net of tax:',
                                         '',
                                         '... 11 more, 13 in total'],
                                        '... 7 more, 9 in total']]}],
                 'propositions': [{'text': '3M Company had net sales of $8,325 million for the three months ended '
                                           'June 30, 2023.',
                                   'source_offset_hint': '--- PAGE 2 --- Table of Contents 3M COMPANY FORM 10-Q For '
                                                         'the Quarterly Period Ended June 30, 2023 PART I. Financial '
                                                         'Information Item 1. Financial Statements. 3M Company ... '
                                                         '(289 characters)',
                                   'page_number': None},
                                  {'text': '3M Company had net sales of $8,702 million for the three months ended '
                                           'June 30, 2022.',
                                   'source_offset_hint': '--- PAGE 2 --- Table of Contents 3M COMPANY FORM 10-Q For '
                                                         'the Quarterly Period Ended June 30, 2023 PART I. Financial '
                                                         'Information Item 1. Financial Statements. 3M Company ... '
                                                         '(289 characters)',
                                   'page_number': None},
                                  '... 79 more, 81 in total']},
        response=True,
        seconds=0.008,
    ),
    Step(
        number='2.5.2.2.4',
        function=segmenter_llm._attribute_pages,
        does='Works out which page each fact came from, in code.',
        request={'propositions': [{'text': '3M Company had net sales of $8,325 million for the three months ended '
                                           'June 30, 2023.',
                                   'source_offset_hint': '--- PAGE 2 --- Table of Contents 3M COMPANY FORM 10-Q For '
                                                         'the Quarterly Period Ended June 30, 2023 PART I. Financial '
                                                         'Information Item 1. Financial Statements. 3M Company ... '
                                                         '(289 characters)',
                                   'page_number': None},
                                  {'text': '3M Company had net sales of $8,702 million for the three months ended '
                                           'June 30, 2022.',
                                   'source_offset_hint': '--- PAGE 2 --- Table of Contents 3M COMPANY FORM 10-Q For '
                                                         'the Quarterly Period Ended June 30, 2023 PART I. Financial '
                                                         'Information Item 1. Financial Statements. 3M Company ... '
                                                         '(289 characters)',
                                   'page_number': None},
                                  '... 79 more, 81 in total'],
                 'pages': [{'page_number': 2,
                            'text': 'Table of Contents\n'
                                    '3M COMPANY\n'
                                    'FORM 10-Q\n'
                                    'For the Quarterly Period Ended June 30, 2023\n'
                                    'PART I. Financial Information\n'
                                    'Item 1. Financial Statements.\n'
                                    '3M Company and Subsidiarie... (521 characters)',
                            'tables': [[['Net sales', '', '... 11 more, 13 in total'],
                                        ['', '', '... 11 more, 13 in total'],
                                        '... 25 more, 27 in total']]},
                           {'page_number': 3,
                            'text': 'Table of Contents\n'
                                    '3M Company and Subsidiaries\n'
                                    'Consolidated Statement of Comprehensive Income (Loss)\n'
                                    '(Unaudited)\n'
                                    'Three months ended Six months ended\n'
                                    'June 30, June 30,\n'
                                    '(Mil... (520 characters)',
                            'tables': [[['Net income (loss) including noncontrolling interest',
                                         '',
                                         '... 11 more, 13 in total'],
                                        ['Other comprehensive income (loss), net of tax:',
                                         '',
                                         '... 11 more, 13 in total'],
                                        '... 7 more, 9 in total']]}]},
        response=[2, 2, '... 79 more, 81 in total'],
        seconds=0.008,
    ),
    Step(
        number='2.5.2.3',
        function=segmenter_llm._segment_one_batch,
        does='Handles one batch. Runs once per batch, and the first two are shown here.',
        request={'batch': [{'page_number': 4,
                            'text': 'Table of Contents\n'
                                    '3M Company and Subsidiaries\n'
                                    'Consolidated Balance Sheet\n'
                                    '(Unaudited)\n'
                                    '(Dollars in millions, except per share amount) June 30, 2023 December 31, 2022\n'
                                    'Assets... (521 characters)',
                            'tables': [[['Assets', '', '... 5 more, 7 in total'],
                                        ['Current assets', '', '... 5 more, 7 in total'],
                                        '... 49 more, 51 in total']]},
                           {'page_number': 5,
                            'text': 'Table of Contents\n'
                                    '3M Company and Subsidiaries\n'
                                    'Consolidated Statement of Cash Flows\n'
                                    '(Unaudited)\n'
                                    'Six months ended\n'
                                    'June 30,\n'
                                    '(Millions) 2023 2022\n'
                                    'Cash Flows from Operating Ac... (521 characters)',
                            'tables': [[['Cash Flows from Operating Activities', '', '... 5 more, 7 in total'],
                                        ['Net income (loss) including noncontrolling interest',
                                         '',
                                         '... 5 more, 7 in total'],
                                        '... 38 more, 40 in total']]}],
                 'document_context': {'company': '3M Company',
                                      'filing_type': '10-Q',
                                      'filing_date': '2023-06-30',
                                      'source_uri': '3M_2023Q2_10Q',
                                      'fiscal_year': 2023,
                                      'quarter': 2},
                 'llm_client': '<RoutedLLMClient>',
                 'max_attempts': 2,
                 'escalation_llm_client': '<RoutedLLMClient>'},
        response=[{'text': '3M Company had cash and cash equivalents of $4,258 million at June 30, 2023.',
                   'source_offset_hint': 'PAGE 4 balance sheet',
                   'page_number': 4},
                  {'text': '3M Company had cash and cash equivalents of $3,655 million at December 31, 2022.',
                   'source_offset_hint': 'PAGE 4 balance sheet',
                   'page_number': 4},
                  '... 146 more, 148 in total'],
        seconds=114.773,
        times_called=3,
    ),
    Step(
        number='2.5.2.3.1',
        function=segmenter_llm._render_page_batch,
        does='Turns a batch of pages into one block of text.',
        request={'pages': [{'page_number': 4,
                            'text': 'Table of Contents\n'
                                    '3M Company and Subsidiaries\n'
                                    'Consolidated Balance Sheet\n'
                                    '(Unaudited)\n'
                                    '(Dollars in millions, except per share amount) June 30, 2023 December 31, 2022\n'
                                    'Assets... (521 characters)',
                            'tables': [[['Assets', '', '... 5 more, 7 in total'],
                                        ['Current assets', '', '... 5 more, 7 in total'],
                                        '... 49 more, 51 in total']]},
                           {'page_number': 5,
                            'text': 'Table of Contents\n'
                                    '3M Company and Subsidiaries\n'
                                    'Consolidated Statement of Cash Flows\n'
                                    '(Unaudited)\n'
                                    'Six months ended\n'
                                    'June 30,\n'
                                    '(Millions) 2023 2022\n'
                                    'Cash Flows from Operating Ac... (521 characters)',
                            'tables': [[['Cash Flows from Operating Activities', '', '... 5 more, 7 in total'],
                                        ['Net income (loss) including noncontrolling interest',
                                         '',
                                         '... 5 more, 7 in total'],
                                        '... 38 more, 40 in total']]}]},
        response=('--- PAGE 4 ---\n'
                  'Table of Contents\n'
                  '3M Company and Subsidiaries\n'
                  'Consolidated Balance Sheet\n'
                  '(Unaudited)\n'
                  '(Dollars in millions, except per share amount) June 30, 2023 December ... (521 characters)'),
        seconds=0.0,
    ),
    Step(
        number='2.5.2.3.2',
        function=segmenter_llm.segment_propositions,
        does='Asks the model for facts, then drops any fact whose digits are not in the source text.',
        request={'source_span': '--- PAGE 4 ---\n'
                                'Table of Contents\n'
                                '3M Company and Subsidiaries\n'
                                'Consolidated Balance Sheet\n'
                                '(Unaudited)\n'
                                '(Dollars in millions, except per share amount) June 30, 2023 December ... (521 '
                                'characters)',
                 'document_context': {'company': '3M Company',
                                      'filing_type': '10-Q',
                                      'filing_date': '2023-06-30',
                                      'source_uri': '3M_2023Q2_10Q',
                                      'fiscal_year': 2023,
                                      'quarter': 2},
                 'llm_client': '<RoutedLLMClient>',
                 'max_attempts': 2},
        response=[{'text': '3M Company had cash and cash equivalents of $4,258 million at June 30, 2023.',
                   'source_offset_hint': '--- PAGE 4 --- Table of Contents 3M Company and Subsidiaries Consolidated '
                                         'Balance Sheet (Unaudited) (Dollars in millions, except per share amount) '
                                         'June 30, 2023 December ... (242 characters)',
                   'page_number': None},
                  {'text': '3M Company had cash and cash equivalents of $3,655 million at December 31, 2022.',
                   'source_offset_hint': '--- PAGE 4 --- Table of Contents 3M Company and Subsidiaries Consolidated '
                                         'Balance Sheet (Unaudited) (Dollars in millions, except per share amount) '
                                         'June 30, 2023 December ... (242 characters)',
                   'page_number': None},
                  '... 132 more, 134 in total'],
        seconds=87.298,
    ),
    Step(
        number='2.5.2.3.2.1',
        function=routing.RoutedLLMClient.complete,
        does='Sends one request to the language model and returns its reply, checked against a fixed shape.',
        request={'payload': {'schema_version': '1.0',
                             'system_prompt': 'You split financial document text into atomic propositions.\n'
                                              '\n'
                                              'An atomic proposition is a single self-contained factual statement '
                                              'that concerns\n'
                                              'exactly one entity, one metr... (521 characters)',
                             'source_span': '--- PAGE 4 ---\n'
                                            'Table of Contents\n'
                                            '3M Company and Subsidiaries\n'
                                            'Consolidated Balance Sheet\n'
                                            '(Unaudited)\n'
                                            '(Dollars in millions, except per share amount) June 30, 2023 December '
                                            '... (521 characters)',
                             'document_context': {'company': '3M Company',
                                                  'filing_type': '10-Q',
                                                  'filing_date': '2023-06-30',
                                                  'source_uri': '3M_2023Q2_10Q',
                                                  'fiscal_year': 2023,
                                                  'quarter': 2}}},
        response={'propositions': [{'text': '3M Company had cash and cash equivalents of $4,258 million at June 30, '
                                            '2023.',
                                    'source_offset_hint': '--- PAGE 4 --- Table of Contents 3M Company and '
                                                          'Subsidiaries Consolidated Balance Sheet (Unaudited) '
                                                          '(Dollars in millions, except per share amount) June 30, '
                                                          '2023 December ... (242 characters)'},
                                   {'text': '3M Company had cash and cash equivalents of $3,655 million at December '
                                            '31, 2022.',
                                    'source_offset_hint': '--- PAGE 4 --- Table of Contents 3M Company and '
                                                          'Subsidiaries Consolidated Balance Sheet (Unaudited) '
                                                          '(Dollars in millions, except per share amount) June 30, '
                                                          '2023 December ... (242 characters)'},
                                   '... 140 more, 142 in total']},
        seconds=87.148,
        model_call={'schema': 'PropositionSegmenterOutputNoReasoning',
                    'model': 'gpt-4.1-mini-2025-04-14',
                    'temperature': 0.0},
    ),
    Step(
        number='2.5.2.3.2.2',
        function=numeric.appears_verbatim,
        does="Checks that a fact's numbers appear word for word in the source text.",
        request={'candidate': '3M Company had cash and cash equivalents of $4,258 million at June 30, 2023.',
                 'source_text': '--- PAGE 4 ---\n'
                                'Table of Contents\n'
                                '3M Company and Subsidiaries\n'
                                'Consolidated Balance Sheet\n'
                                '(Unaudited)\n'
                                '(Dollars in millions, except per share amount) June 30, 2023 December ... (521 '
                                'characters)'},
        response=True,
        seconds=0.002,
        times_called=142,
    ),
    Step(
        number='2.5.2.3.3',
        function=segmenter_llm._table_rows_complete,
        does='Checks that table rows kept their real values. If not, the batch is retried on the stronger model.',
        request={'pages': [{'page_number': 4,
                            'text': 'Table of Contents\n'
                                    '3M Company and Subsidiaries\n'
                                    'Consolidated Balance Sheet\n'
                                    '(Unaudited)\n'
                                    '(Dollars in millions, except per share amount) June 30, 2023 December 31, 2022\n'
                                    'Assets... (521 characters)',
                            'tables': [[['Assets', '', '... 5 more, 7 in total'],
                                        ['Current assets', '', '... 5 more, 7 in total'],
                                        '... 49 more, 51 in total']]},
                           {'page_number': 5,
                            'text': 'Table of Contents\n'
                                    '3M Company and Subsidiaries\n'
                                    'Consolidated Statement of Cash Flows\n'
                                    '(Unaudited)\n'
                                    'Six months ended\n'
                                    'June 30,\n'
                                    '(Millions) 2023 2022\n'
                                    'Cash Flows from Operating Ac... (521 characters)',
                            'tables': [[['Cash Flows from Operating Activities', '', '... 5 more, 7 in total'],
                                        ['Net income (loss) including noncontrolling interest',
                                         '',
                                         '... 5 more, 7 in total'],
                                        '... 38 more, 40 in total']]}],
                 'propositions': [{'text': '3M Company had cash and cash equivalents of $4,258 million at June 30, '
                                           '2023.',
                                   'source_offset_hint': '--- PAGE 4 --- Table of Contents 3M Company and '
                                                         'Subsidiaries Consolidated Balance Sheet (Unaudited) '
                                                         '(Dollars in millions, except per share amount) June 30, '
                                                         '2023 December ... (242 characters)',
                                   'page_number': None},
                                  {'text': '3M Company had cash and cash equivalents of $3,655 million at December '
                                           '31, 2022.',
                                   'source_offset_hint': '--- PAGE 4 --- Table of Contents 3M Company and '
                                                         'Subsidiaries Consolidated Balance Sheet (Unaudited) '
                                                         '(Dollars in millions, except per share amount) June 30, '
                                                         '2023 December ... (242 characters)',
                                   'page_number': None},
                                  '... 132 more, 134 in total']},
        response=False,
        seconds=0.002,
    ),
    Step(
        number='2.5.2.3.4',
        function=segmenter_llm.segment_propositions,
        does='Asks the model for facts, then drops any fact whose digits are not in the source text.',
        request={'source_span': '--- PAGE 4 ---\n'
                                'Table of Contents\n'
                                '3M Company and Subsidiaries\n'
                                'Consolidated Balance Sheet\n'
                                '(Unaudited)\n'
                                '(Dollars in millions, except per share amount) June 30, 2023 December ... (521 '
                                'characters)',
                 'document_context': {'company': '3M Company',
                                      'filing_type': '10-Q',
                                      'filing_date': '2023-06-30',
                                      'source_uri': '3M_2023Q2_10Q',
                                      'fiscal_year': 2023,
                                      'quarter': 2},
                 'llm_client': '<RoutedLLMClient>',
                 'max_attempts': 2},
        response=[{'text': '3M Company had cash and cash equivalents of $4,258 million at June 30, 2023.',
                   'source_offset_hint': 'PAGE 4 balance sheet',
                   'page_number': None},
                  {'text': '3M Company had cash and cash equivalents of $3,655 million at December 31, 2022.',
                   'source_offset_hint': 'PAGE 4 balance sheet',
                   'page_number': None},
                  '... 146 more, 148 in total'],
        seconds=27.448,
    ),
    Step(
        number='2.5.2.3.4.1',
        function=routing.RoutedLLMClient.complete,
        does='Sends one request to the language model and returns its reply, checked against a fixed shape.',
        request={'payload': {'schema_version': '1.0',
                             'system_prompt': 'You split financial document text into atomic propositions.\n'
                                              '\n'
                                              'An atomic proposition is a single self-contained factual statement '
                                              'that concerns\n'
                                              'exactly one entity, one metr... (521 characters)',
                             'source_span': '--- PAGE 4 ---\n'
                                            'Table of Contents\n'
                                            '3M Company and Subsidiaries\n'
                                            'Consolidated Balance Sheet\n'
                                            '(Unaudited)\n'
                                            '(Dollars in millions, except per share amount) June 30, 2023 December '
                                            '... (521 characters)',
                             'document_context': {'company': '3M Company',
                                                  'filing_type': '10-Q',
                                                  'filing_date': '2023-06-30',
                                                  'source_uri': '3M_2023Q2_10Q',
                                                  'fiscal_year': 2023,
                                                  'quarter': 2}}},
        response={'reasoning': 'The source contains two unaudited financial tables for 3M Company. I emitted one '
                               'proposition for each row-and-period value, preserved all numbers exactly as shown, '
                               'treate... (414 characters)',
                  'propositions': [{'text': '3M Company had cash and cash equivalents of $4,258 million at June 30, '
                                            '2023.',
                                    'source_offset_hint': 'PAGE 4 balance sheet'},
                                   {'text': '3M Company had cash and cash equivalents of $3,655 million at December '
                                            '31, 2022.',
                                    'source_offset_hint': 'PAGE 4 balance sheet'},
                                   '... 146 more, 148 in total']},
        seconds=27.271,
        model_call={'schema': 'PropositionSegmenterOutput', 'model': 'gpt-5.6-luna', 'temperature': 0.0},
    ),
    Step(
        number='2.5.2.3.4.2',
        function=numeric.appears_verbatim,
        does="Checks that a fact's numbers appear word for word in the source text.",
        request={'candidate': '3M Company had cash and cash equivalents of $4,258 million at June 30, 2023.',
                 'source_text': '--- PAGE 4 ---\n'
                                'Table of Contents\n'
                                '3M Company and Subsidiaries\n'
                                'Consolidated Balance Sheet\n'
                                '(Unaudited)\n'
                                '(Dollars in millions, except per share amount) June 30, 2023 December ... (521 '
                                'characters)'},
        response=True,
        seconds=0.002,
        times_called=148,
    ),
    Step(
        number='2.5.2.3.5',
        function=segmenter_llm._attribute_pages,
        does='Works out which page each fact came from, in code.',
        request={'propositions': [{'text': '3M Company had cash and cash equivalents of $4,258 million at June 30, '
                                           '2023.',
                                   'source_offset_hint': 'PAGE 4 balance sheet',
                                   'page_number': None},
                                  {'text': '3M Company had cash and cash equivalents of $3,655 million at December '
                                           '31, 2022.',
                                   'source_offset_hint': 'PAGE 4 balance sheet',
                                   'page_number': None},
                                  '... 146 more, 148 in total'],
                 'pages': [{'page_number': 4,
                            'text': 'Table of Contents\n'
                                    '3M Company and Subsidiaries\n'
                                    'Consolidated Balance Sheet\n'
                                    '(Unaudited)\n'
                                    '(Dollars in millions, except per share amount) June 30, 2023 December 31, 2022\n'
                                    'Assets... (521 characters)',
                            'tables': [[['Assets', '', '... 5 more, 7 in total'],
                                        ['Current assets', '', '... 5 more, 7 in total'],
                                        '... 49 more, 51 in total']]},
                           {'page_number': 5,
                            'text': 'Table of Contents\n'
                                    '3M Company and Subsidiaries\n'
                                    'Consolidated Statement of Cash Flows\n'
                                    '(Unaudited)\n'
                                    'Six months ended\n'
                                    'June 30,\n'
                                    '(Millions) 2023 2022\n'
                                    'Cash Flows from Operating Ac... (521 characters)',
                            'tables': [[['Cash Flows from Operating Activities', '', '... 5 more, 7 in total'],
                                        ['Net income (loss) including noncontrolling interest',
                                         '',
                                         '... 5 more, 7 in total'],
                                        '... 38 more, 40 in total']]}]},
        response=[4, 4, '... 146 more, 148 in total'],
        seconds=0.025,
    ),
    Step(
        number='2.6',
        function=full_build._assemble_propositions,
        does='Turns each fact into a full record with numbers, metadata and an ID.',
        request={'segmented': [{'text': '3M Company had net sales of $8,325 million for the three months ended June '
                                        '30, 2023.',
                                'source_offset_hint': '--- PAGE 2 --- Table of Contents 3M COMPANY FORM 10-Q For the '
                                                      'Quarterly Period Ended June 30, 2023 PART I. Financial '
                                                      'Information Item 1. Financial Statements. 3M Company ... (289 '
                                                      'characters)',
                                'page_number': 2},
                               {'text': '3M Company had net sales of $8,702 million for the three months ended June '
                                        '30, 2022.',
                                'source_offset_hint': '--- PAGE 2 --- Table of Contents 3M COMPANY FORM 10-Q For the '
                                                      'Quarterly Period Ended June 30, 2023 PART I. Financial '
                                                      'Information Item 1. Financial Statements. 3M Company ... (289 '
                                                      'characters)',
                                'page_number': 2},
                               '... 269 more, 271 in total'],
                 'document_context': {'company': '3M Company',
                                      'filing_type': '10-Q',
                                      'filing_date': '2023-06-30',
                                      'source_uri': '3M_2023Q2_10Q',
                                      'fiscal_year': 2023,
                                      'quarter': 2},
                 'llm_client': '<RoutedLLMClient>'},
        response=[{'proposition_id': 'p_ba40c3e9f933',
                   'text': '3M Company had net sales of $8,325 million for the three months ended June 30, 2023.',
                   'provenance_tier': 'retrieved',
                   'ingestion_path': 'A_unstructured',
                   'numeric': None,
                   'confidence': {'ocr_confidence': 1.0, 'extraction_tier': 'rules'},
                   'metadata': {'company': '3M Company',
                                'ticker': None,
                                'filing_type': '10-Q',
                                'filing_date': '2023-06-30',
                                'section': '',
                                'page': 2,
                                'source_uri': '3M_2023Q2_10Q'}},
                  {'proposition_id': 'p_4d07646b742d',
                   'text': '3M Company had net sales of $8,702 million for the three months ended June 30, 2022.',
                   'provenance_tier': 'retrieved',
                   'ingestion_path': 'A_unstructured',
                   'numeric': None,
                   'confidence': {'ocr_confidence': 1.0, 'extraction_tier': 'rules'},
                   'metadata': {'company': '3M Company',
                                'ticker': None,
                                'filing_type': '10-Q',
                                'filing_date': '2023-06-30',
                                'section': '',
                                'page': 2,
                                'source_uri': '3M_2023Q2_10Q'}},
                  '... 269 more, 271 in total'],
        seconds=0.023,
    ),
    Step(
        number='2.6.1',
        function=numeric_extractor.extract,
        does='Reads the value, unit and period from the fact text, using rules first.',
        request={'text': '3M Company had net sales of $8,325 million for the three months ended June 30, 2023.',
                 'document_context': {'company': '3M Company',
                                      'filing_type': '10-Q',
                                      'filing_date': '2023-06-30',
                                      'source_uri': '3M_2023Q2_10Q',
                                      'fiscal_year': 2023,
                                      'quarter': 2},
                 'llm_client': '<RoutedLLMClient>'},
        response={'numeric': None, 'extraction_tier': None},
        seconds=0.0,
        times_called=271,
    ),
    Step(
        number='2.6.1.1',
        function=numeric_extractor.extract_tier2,
        does='The rule based reader. No model is involved.',
        request={'text': '3M Company had net sales of $8,325 million for the three months ended June 30, 2023.'},
        response=None,
        seconds=0.0,
    ),
    Step(
        number='2.6.2',
        function=tagger.tag,
        does='Attaches company, filing type, filing date, page and source document.',
        request={'document_context': {'company': '3M Company',
                                      'filing_type': '10-Q',
                                      'filing_date': '2023-06-30',
                                      'source_uri': '3M_2023Q2_10Q',
                                      'fiscal_year': 2023,
                                      'quarter': 2},
                 'section': '',
                 'page': 2},
        response={'company': '3M Company',
                  'ticker': None,
                  'filing_type': '10-Q',
                  'filing_date': '2023-06-30',
                  'section': '',
                  'page': 2,
                  'source_uri': '3M_2023Q2_10Q'},
        seconds=0.0,
        times_called=271,
    ),
    Step(
        number='2.6.3',
        function=full_build._proposition_id,
        does='Builds a stable ID from the document, page and text.',
        request={'source_uri': '3M_2023Q2_10Q',
                 'page': 2,
                 'text': '3M Company had net sales of $8,325 million for the three months ended June 30, 2023.',
                 'occurrence': 0},
        response='p_ba40c3e9f933',
        seconds=0.0,
        times_called=271,
    ),
    Step(
        number='2.7',
        function=index_build.build_index,
        does='Embeds every fact and writes it to the vector index and the database.',
        request={'propositions': [{'proposition_id': 'p_ba40c3e9f933',
                                   'text': '3M Company had net sales of $8,325 million for the three months ended '
                                           'June 30, 2023.',
                                   'provenance_tier': 'retrieved',
                                   'ingestion_path': 'A_unstructured',
                                   'numeric': None,
                                   'confidence': {'ocr_confidence': 1.0, 'extraction_tier': 'rules'},
                                   'metadata': {'company': '3M Company',
                                                'ticker': None,
                                                'filing_type': '10-Q',
                                                'filing_date': '2023-06-30',
                                                'section': '',
                                                'page': 2,
                                                'source_uri': '3M_2023Q2_10Q'}},
                                  {'proposition_id': 'p_4d07646b742d',
                                   'text': '3M Company had net sales of $8,702 million for the three months ended '
                                           'June 30, 2022.',
                                   'provenance_tier': 'retrieved',
                                   'ingestion_path': 'A_unstructured',
                                   'numeric': None,
                                   'confidence': {'ocr_confidence': 1.0, 'extraction_tier': 'rules'},
                                   'metadata': {'company': '3M Company',
                                                'ticker': None,
                                                'filing_type': '10-Q',
                                                'filing_date': '2023-06-30',
                                                'section': '',
                                                'page': 2,
                                                'source_uri': '3M_2023Q2_10Q'}},
                                  '... 269 more, 271 in total'],
                 'embedder': '<BGEEmbedder>',
                 'dense_index_path': 'C:\\Users\\NAHEEM~1.QUA\\AppData\\Local\\Temp\\walk_syl9yun7\\dense_index',
                 'postgres_dsn': 'postgresql://financial_rag:financial_rag_dev@localhost:5432/financial_rag_test',
                 'indexed_doc_names': ['3M_2023Q2_10Q'],
                 'skipped_pages_by_reason': {'cover': 1, 'table_of_contents': 1},
                 'gold_overlap_clean': None},
        response={'build_id': '7fc1e3d61d59e3f8',
                  'proposition_count': 271,
                  'embedder_model_name': 'BAAI/bge-base-en-v1.5',
                  'embedder_revision': 'a5beb1e3e68b9ab74eb54cfd186867f64f240e1a',
                  'embedder_dimension': 768,
                  'indexed_doc_names': ['3M_2023Q2_10Q'],
                  'skipped_pages_by_reason': {'cover': 1, 'table_of_contents': 1},
                  'gold_overlap_clean': None},
        seconds=25.754,
    ),
    Step(
        number='2.7.1',
        function=bge_embedder.BGEEmbedder.encode_passages,
        does='Turns each fact into a vector.',
        request={'texts': ['3M Company had net sales of $8,325 million for the three months ended June 30, 2023.',
                           '3M Company had net sales of $8,702 million for the three months ended June 30, 2022.',
                           '... 269 more, 271 in total']},
        response=[[-0.00464, -0.00844, '... 766 more, 768 in total'],
                  [0.0071, -0.00273, '... 766 more, 768 in total'],
                  '... 269 more, 271 in total'],
        seconds=6.677,
    ),
    Step(
        number='2.7.2',
        function=dense_index_faiss.PersistentDenseIndex.upsert,
        does="Adds one fact's vector to the index. Called once per fact, 271 times in this sample, and not traced here.",
        request={'proposition_id': 'p_01613b952dce', 'vector': [-0.00464, -0.00844, '... 766 more, 768 in total']},
        response=None,
    ),
    Step(
        number='2.7.3',
        function=metadata_store_postgres.PostgresMetadataStore.upsert,
        does='Saves one fact as a database row. Called once per fact, 271 times in this sample, and not traced here.',
        request={'proposition': {'proposition_id': 'p_01613b952dce',
                                 'text': '3M Company reported net cash provided by operating activities of 2,784 '
                                         'million for the six months ended June 30, 2023.',
                                 'provenance_tier': 'retrieved',
                                 'ingestion_path': 'A_unstructured',
                                 'numeric': None,
                                 'confidence': {'ocr_confidence': 1.0, 'extraction_tier': 'rules'},
                                 'metadata': {'company': '3M Company',
                                              'ticker': None,
                                              'filing_type': '10-Q',
                                              'filing_date': '2023-06-30',
                                              'section': '',
                                              'page': 5,
                                              'source_uri': '3M_2023Q2_10Q'}}},
        response=None,
    ),
    Step(
        number='2.7.4',
        function=dense_index_faiss.PersistentDenseIndex.save,
        does='Writes the vector index to disk.',
        request={'path': 'C:\\Users\\NAHEEM~1.QUA\\AppData\\Local\\Temp\\walk_syl9yun7\\dense_index'},
        response=None,
        seconds=0.004,
    ),
    Step(
        number='2.7.5',
        function=index_build.compute_build_id,
        does='Makes an ID from the fact IDs and the embedding model, so identical input gives an identical ID.',
        request={'propositions': [{'proposition_id': 'p_ba40c3e9f933',
                                   'text': '3M Company had net sales of $8,325 million for the three months ended '
                                           'June 30, 2023.',
                                   'provenance_tier': 'retrieved',
                                   'ingestion_path': 'A_unstructured',
                                   'numeric': None,
                                   'confidence': {'ocr_confidence': 1.0, 'extraction_tier': 'rules'},
                                   'metadata': {'company': '3M Company',
                                                'ticker': None,
                                                'filing_type': '10-Q',
                                                'filing_date': '2023-06-30',
                                                'section': '',
                                                'page': 2,
                                                'source_uri': '3M_2023Q2_10Q'}},
                                  {'proposition_id': 'p_4d07646b742d',
                                   'text': '3M Company had net sales of $8,702 million for the three months ended '
                                           'June 30, 2022.',
                                   'provenance_tier': 'retrieved',
                                   'ingestion_path': 'A_unstructured',
                                   'numeric': None,
                                   'confidence': {'ocr_confidence': 1.0, 'extraction_tier': 'rules'},
                                   'metadata': {'company': '3M Company',
                                                'ticker': None,
                                                'filing_type': '10-Q',
                                                'filing_date': '2023-06-30',
                                                'section': '',
                                                'page': 2,
                                                'source_uri': '3M_2023Q2_10Q'}},
                                  '... 269 more, 271 in total'],
                 'embedder': '<BGEEmbedder>'},
        response='7fc1e3d61d59e3f8',
        seconds=0.0,
    ),
    Step(
        number='3',
        function=index_build.load_index,
        does='Reopens the saved indexes for the question flow. It also rebuilds the keyword index from the database.',
        request={'dense_index_path': 'data/index/dense_index',
                 'postgres_dsn': 'postgresql://.../financial_rag_v2',
                 'dimension': 768},
        response=('IndexBundle(dense_index=<PersistentDenseIndex>, sparse_index=<SparseIndex>, '
                  'metadata_store=<PostgresMetadataStore>)'),
    ),
    Step(
        number='4',
        function=placeholder_builder.build_registry,
        does='Maps every name a company appears under to one placeholder. run_full_build does not call it. The API builds its own registry at startup, in api/server.py.',
        request={'entities': [{'surface_form': '3M Company', 'ticker': 'MMM'},
                              {'surface_form': '3M', 'ticker': 'MMM'}]},
        response=[{'surface_form': '3M', 'placeholder': '<ENT_1>', 'canonical': {'ticker': 'MMM', 'cik': None}},
                  {'surface_form': '3M Company',
                   'placeholder': '<ENT_1>',
                   'canonical': {'ticker': 'MMM', 'cik': None}}],
        seconds=0.0,
    ),
]


def main() -> None:
    walk(FLOW, detail="--detail" in sys.argv)


if __name__ == "__main__":
    main()
