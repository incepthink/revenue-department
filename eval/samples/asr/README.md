# ASR sample (synthetic)

`asr_synthetic_pune.csv` has **invented** rates for the three villages in the sample dockets. It exists to
exercise the importer and the admin page; never import it into a database that holds real cases. Every row is
`to_verify` and says `SYNTHETIC SAMPLE` in `source_ref`.

The real ASR table for the pilot division was not available in the Phase 2 session. Import it with
`rci asr-import <file.csv> --commit` (dry run without `--commit`) or from Admin → ASR rates.
