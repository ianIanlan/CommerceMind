# Banking77 attribution

`external_banking77_v1.jsonl` contains an unmodified, deterministically sampled subset of the official Banking77 test split.

- Dataset: Banking77
- Authors: Iñigo Casanueva, Tadas Temčinas, Daniela Gerz, Matthew Henderson, Ivan Vulić
- Paper: *Efficient Intent Detection with Dual Sentence Encoders* (NLP4ConvAI 2020)
- Upstream: https://github.com/PolyAI-LDN/task-specific-datasets
- Dataset card: https://huggingface.co/datasets/PolyAI/banking77
- License: Creative Commons Attribution 4.0 International (CC BY 4.0)
- Upstream commit used: `57ec275d8078af65b7731c2a98be812d844a6d6b`
- Original test CSV SHA-256: `d12d6e3bc4c3103966ae786dc435913c0c563dfa328f5a3646d0e62cfeeb474d`

Changes made by CommerceMind:

1. Selected 15 records from each of eight source categories with random seed `20261001`.
2. Preserved the source query text without translation or rewriting.
3. Mapped source category labels to the closest CommerceMind intent labels.
4. Added stable case IDs and provenance tags.

This subset is used only as a cross-domain external evaluation set. It is not represented as Chinese e-commerce production traffic.
