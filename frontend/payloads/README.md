# Holo Benchmark — Scenario Payloads

The JSON files in this folder are clean benchmark scenario payloads.

These payloads are the only artifacts that should be passed into a model as scenario input. They contain only the case facts and documents presented to each model during evaluation: the proposed action, email thread, vendor records, attachment summaries, domain intelligence, and organizational policies.

They do not contain expected verdicts, answer keys, fraud labels, scoring rubrics, or any post-hoc annotation. That information was never in the model context during the benchmark.

[BENCHMARK_PROTOCOL.md](BENCHMARK_PROTOCOL.md) explains the turn structure, role prompts, convergence rules, and full reproduction method.

Expected verdicts and display badges shown in the Blindspot Atlas are post-hoc scoring annotations only and were never included in any model prompt.
