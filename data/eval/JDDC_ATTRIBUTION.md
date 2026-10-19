# JDDC attribution and data policy

- Dataset: JDDC / JDDC 2.1
- Domain: real-world Chinese e-commerce customer-service dialogue collected from JD.com
- JDDC paper: https://aclanthology.org/2020.lrec-1.58/
- JDDC 2.1 paper: https://aclanthology.org/2022.emnlp-main.825/
- Official application: https://github.com/hrlinlp/jddc2.1

JDDC 2.1 requires an application using an organization email. CommerceMind does not redistribute the raw dialogue, images, or derived utterance-level test file. Only the importer, mapping template, manifest metadata, aggregate metrics, and error categories may be committed after access is granted.

The JDDC test split must be frozen before the first model run. It must not be used to tune prompts, patterns, fusion weights, or thresholds.
