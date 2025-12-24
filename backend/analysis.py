from . import config
from .utils import rand_delay


def analyze_with_gpt(meeting_name: str, meeting_topic: str, participants: str, transcript: str) -> str:
    rand_delay("before GPT analysis")
    print("[gpt] starting meeting analysis")

    prompt = f"""
You are an expert meeting analyst and diarization corrector.

Your job is to take a raw transcript that may contain:
- inconsistent speaker boundaries,
- incorrect speaker switches,
- short fragmented lines,
- semantic drift between segments,
- accidental alternation between “Speaker X” labels,
- or split utterances that belong together.

Before performing any meeting analysis, apply a *speaker smoothing and correction pass*:

=== DIAIRIZATION CONSOLIDATION LAYER ===
1) Reconstruct the conversation with inferred speakers using the minimal number of speaker labels needed.
2) Merge any consecutive segments that clearly belong to the same speaker based on coherence, grammar, tone, topic continuity, or clear conversational flow.
3) Correct speaker clusters that are obviously mis-assigned:
   - If two adjacent segments have nearly identical semantic footprint, merge them.
   - If a speaker “switch” is only 1 sentence long but contextually implausible, treat it as the same speaker.
4) Preserve long-range continuity:
   - A speaker should not rapidly alternate unless the transcript indicates an actual exchange.
   - When the transcript contains ambiguous or messy parts, choose the assignment that results in the fewest contradictions.
5) Mark every correction you make using inline comments like:
   [merged], [reassigned], [consolidated], [uncertain].

After this correction layer, output the cleaned dialog.

=== MEETING ANALYSIS (same as before) ===
Based solely on the cleaned dialog:
1) Identify action items (per participant, and others).
2) Identify dependencies/blockers.
3) Identify deadlines or time references.
4) Identify misalignments, risks (process/technical/interpersonal).

Rules:
- Do NOT hallucinate new events; use only what is present.
- If an insight is inferred but not explicit, tag it as "(inferred)".
- Always output everything in English.

Meeting name: {meeting_name}
Topic: {meeting_topic}
Participants: {participants}

--- TRANSCRIPT START ---
{transcript}
--- TRANSCRIPT END ---
"""

    resp = config.client.responses.create(
        model="gpt-5.1",
        input=prompt,
    )
    text = resp.output_text.strip()
    print("[gpt] analysis done, length:", len(text))
    return text
