# AmazonHelp external diagnostic attribution

`external_amazonhelp_v1.jsonl` and `external_amazonhelp_holdout_v1.jsonl` contain disjoint redacted subsets derived from public AmazonHelp customer-support conversations.

- Immediate upstream: [ConvoSense](https://github.com/venkatrepaka-umich/ConvoSense), commit `82b1b3e39db752a08a70d95bfafd160c334ca86d`
- Original source: [Customer Support on Twitter](https://www.kaggle.com/datasets/thoughtvector/customer-support-on-twitter)
- Related peer-reviewed use of the same source: [TWEETSUMM, Findings of EMNLP 2021](https://aclanthology.org/2021.findings-emnlp.24/)
- Data license: CC BY-NC-SA 4.0

The source messages are public social-media customer-support conversations, not private Amazon production tickets. ConvoSense removes handles and normalizes links; CommerceMind additionally redacts order-like digit sequences, emails, phone-like values, and links.

The six CommerceMind intent labels are project-authored silver labels. High-precision rules produced candidates, followed by a one-pass review of the full dialogues. They are not official Amazon annotations and were not independently double-annotated.
