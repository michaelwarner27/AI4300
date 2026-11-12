# Document Workspace

Download these four PDFs into this directory before running ingestion. Keep the
filenames shown here so the source labels in your retrieval traces are easy to
compare with classmates.

| Filename | Document | Source |
| --- | --- | --- |
| `nist-ai-rmf.pdf` | NIST AI Risk Management Framework 1.0 | <https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.100-1.pdf> |
| `attention-is-all-you-need.pdf` | Attention Is All You Need | <https://arxiv.org/pdf/1706.03762> |
| `retrieval-augmented-generation.pdf` | Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks | <https://arxiv.org/pdf/2005.11401> |
| `react.pdf` | ReAct: Synergizing Reasoning and Acting in Language Models | <https://arxiv.org/pdf/2210.03629> |

You can use a browser or run these commands from the project root:

```sh
curl -L https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.100-1.pdf \
  -o documents/nist-ai-rmf.pdf
curl -L https://arxiv.org/pdf/1706.03762 \
  -o documents/attention-is-all-you-need.pdf
curl -L https://arxiv.org/pdf/2005.11401 \
  -o documents/retrieval-augmented-generation.pdf
curl -L https://arxiv.org/pdf/2210.03629 \
  -o documents/react.pdf
```

Confirm that each file exists and is a PDF:

```sh
file documents/*.pdf
```

The downloaded PDFs are local lab inputs. Git and the student-package builder
exclude them.
