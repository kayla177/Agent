"""Resume generator agent — draft an ATS-tailored resume for one scraped job.

Event-triggered (per job), unlike the scheduled agents: given a job id, it pulls
the stored posting, researches the company, extracts the posting's ATS keywords,
and drafts a Markdown resume that reframes the user's *real* experience pool to
match — never inventing experience. See graph.py for the pipeline.
"""
