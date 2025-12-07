import sys
from email.message import EmailMessage

import smtplib

from . import config


def send_analysis_via_email(
    recipient: str | None,
    meeting_name: str,
    meeting_id: str,
    folder,
) -> None:
    """
    If recipient and SMTP config are available, email the analysis (and optionally transcript)
    to the user. Best-effort only: never raise out of here.
    Now sends a multipart email: plain text + HTML (Apple-style layout).
    """
    if not recipient:
        return

    if not config.EMAIL_ENABLED:
        print("[email] EMAIL_ENABLED is False; skipping email send for", meeting_id)
        return

    try:
        transcript_path = folder / "transcript.txt"
        analysis_path = folder / "analysis.txt"

        transcript = ""
        analysis = ""

        if analysis_path.exists():
            try:
                analysis = analysis_path.read_text(encoding="utf-8")
            except Exception as e:
                print(
                    f"[email] failed to read analysis.txt for {meeting_id}: {e}",
                    file=sys.stderr,
                )

        if transcript_path.exists():
            try:
                transcript = transcript_path.read_text(encoding="utf-8")
            except Exception as e:
                print(
                    f"[email] failed to read transcript.txt for {meeting_id}: {e}",
                    file=sys.stderr,
                )

        # --- Build text version (fallback) ---
        text_parts: list[str] = []
        text_parts.append(
            f"Here are your smallpie notes for meeting '{meeting_name}' (ID: {meeting_id})."
        )
        text_parts.append("")
        if analysis:
            text_parts.append("=== ANALYSIS ===")
            text_parts.append(analysis)
            text_parts.append("")

        if transcript:
            text_parts.append("=== TRANSCRIPT (may be truncated) ===")
            if len(transcript) > 15000:
                text_parts.append(transcript[:15000])
                text_parts.append("\n[transcript truncated]")
            else:
                text_parts.append(transcript)

        text_body = "\n".join(text_parts)

        import html as _html

        esc_meeting_name = _html.escape(meeting_name)
        esc_meeting_id = _html.escape(meeting_id)
        esc_analysis = _html.escape(analysis) if analysis else ""
        esc_transcript = _html.escape(
            transcript[:15000] + ("\n[transcript truncated]" if len(transcript) > 15000 else "")
        ) if transcript else ""

        html_body = f"""\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <title>smallpie – meeting notes</title>
</head>
<body style="margin:0; padding:0; background-color:#f5f5f7;">
  <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color:#f5f5f7; padding:32px 0;">
    <tr>
      <td align="center">
        <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="max-width:640px; background-color:#ffffff; border-radius:18px; overflow:hidden; box-shadow:0 14px 30px rgba(0,0,0,0.08);">
          <!-- Header -->
          <tr>
            <td style="padding:24px 32px 16px 32px; background:linear-gradient(135deg,#111827,#020617);">
              <div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; font-size:13px; letter-spacing:0.12em; text-transform:uppercase; color:#9ca3af; margin-bottom:8px;">
                smallpie
              </div>
              <div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; font-size:22px; font-weight:600; color:#f9fafb; line-height:1.35;">
                Meeting summary
              </div>
              <div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; font-size:13px; color:#9ca3af; margin-top:6px;">
                {esc_meeting_name}
              </div>
            </td>
          </tr>

          <!-- Meta -->
          <tr>
            <td style="padding:16px 32px 8px 32px;">
              <div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; font-size:13px; color:#6b7280; line-height:1.4;">
                ID: <span style="color:#111827; font-weight:500;">{esc_meeting_id}</span>
              </div>
              <div style="height:12px;"></div>
              <div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; font-size:14px; color:#111827; line-height:1.5;">
                Here are your smallpie notes for this meeting.<br/>
                The analysis is shown first, followed by the raw transcript (which may be truncated).
              </div>
            </td>
          </tr>

          <!-- Analysis -->
          {""
          if not esc_analysis
          else f"""
          <tr>
            <td style="padding:8px 32px 8px 32px;">
              <div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; font-size:12px; letter-spacing:0.14em; text-transform:uppercase; color:#6b7280; margin-bottom:6px;">
                Analysis
              </div>
              <div style="border-radius:14px; background-color:#f9fafb; border:1px solid #e5e7eb; padding:12px 14px;">
                <div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; font-size:14px; color:#111827; line-height:1.5; white-space:pre-wrap;">
                  {esc_analysis}
                </div>
              </div>
            </td>
          </tr>
          """
          }

          <!-- Transcript -->
          {""
          if not esc_transcript
          else f"""
          <tr>
            <td style="padding:4px 32px 24px 32px;">
              <div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; font-size:12px; letter-spacing:0.14em; text-transform:uppercase; color:#6b7280; margin-bottom:6px; margin-top:8px;">
                Transcript
              </div>
              <div style="border-radius:14px; background-color:#f9fafb; border:1px solid #e5e7eb; padding:12px 14px;">
                <div style="font-family:SFMono-Regular,Menlo,Monaco,Consolas,'Liberation Mono','Courier New',monospace; font-size:12px; color:#111827; line-height:1.5; white-space:pre-wrap;">
                  {esc_transcript}
                </div>
                {"<div style='font-family:-apple-system,BlinkMacSystemFont,\\'Segoe UI\\',sans-serif; font-size:11px; color:#9ca3af; margin-top:6px;'>Transcript truncated for email display.</div>" if len(transcript) > 15000 else ""}
              </div>
            </td>
          </tr>
          """
          }

          <!-- Footer -->
          <tr>
            <td style="padding:16px 32px 20px 32px; border-top:1px solid #e5e7eb; background-color:#fbfbfd;">
              <div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; font-size:11px; color:#9ca3af; line-height:1.4;">
                Generated locally by smallpie using whisper.cpp and GPT analysis.&nbsp;
                <span style="color:#6b7280;">No meeting audio is sent to third-party transcription services.</span>
              </div>
            </td>
          </tr>

        </table>
      </td>
    </tr>
  </table>
</body>
</html>
"""

        msg = EmailMessage()
        msg["Subject"] = f"[smallpie] Notes for '{meeting_name}'"
        msg["From"] = config.SMTP_FROM
        msg["To"] = recipient

        msg.set_content(text_body)
        msg.add_alternative(html_body, subtype="html")

        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT) as server:
            server.starttls()
            if config.SMTP_USERNAME and config.SMTP_PASSWORD:
                server.login(config.SMTP_USERNAME, config.SMTP_PASSWORD)
            server.send_message(
                msg,
                from_addr=config.SMTP_FROM,
                to_addrs=[recipient],
            )

        print(f"[email] sent meeting {meeting_id} to {recipient}")
    except Exception as e:
        print(f"[email] failed to send email for meeting {meeting_id}: {e}", file=sys.stderr)
