"""Functional: a calendar invitation in the mail becomes an event, and an event becomes a deadline.

The rule the first class pins: the job classifier wins. An invitation only creates an event when the
classifier passed the message over, so a recruiter's interview invite stays an interview and keeps
feeding the application tracker. What the invitation always does is supply the time, because it states
it rather than implying it the way recruiter prose does.
"""
import json
import unittest
from email.message import EmailMessage

from tests._support import SliceTest
import mail


def ics(*lines, **kw):
    head = ["BEGIN:VCALENDAR", "VERSION:2.0"] + (["METHOD:" + kw["method"]] if kw.get("method") else [])
    return "\r\n".join(head + ["BEGIN:VEVENT"] + list(lines) + ["END:VEVENT", "END:VCALENDAR"]) + "\r\n"


INVITE = ics("UID:u1", "DTSTART;TZID=America/New_York:20991005T150000",
             "SUMMARY:Coffee with Prof. Smith", "LOCATION:Gates 4201",
             "ORGANIZER;CN=Ada Smith:mailto:ada@example.edu")


def head(subject, date="2099-10-01"):
    return {"subject": subject, "date": date, "msgid": "<m1@x>", "uid": b"7",
            "from_addr": "ada@example.edu", "from_name": "Ada Smith", "unsub": False}


class ItemsFromInvitations(unittest.TestCase):
    def test_an_invitation_the_classifier_ignores_becomes_an_event(self):
        ev = mail.first_event(INVITE)
        it = mail.make_item("me@x.com", head("Coffee?"), "", "event", "", ev)
        self.assertEqual(it["kind"], "event")
        self.assertEqual(it["title"], "Coffee with Prof. Smith")   # the summary, not the subject
        self.assertEqual(it["due"], "2099-10-05 15:00")
        self.assertEqual(it["company"], "Ada Smith")               # organizer, not a guessed company
        self.assertIn("Gates 4201", it["detail"])
        self.assertEqual(it["role"], "")                           # never fed to the tracker

    def test_a_job_kind_keeps_its_kind_and_takes_the_time_from_the_invitation(self):
        ev = mail.first_event(INVITE)
        prose = "We would love to speak with you. Someone will reach out to schedule a time next week."
        it = mail.make_item("me@x.com", head("Interview invitation - Acme"), prose, "interview", "", ev)
        self.assertEqual(it["kind"], "interview")                  # still an application event
        self.assertEqual(it["due"], "2099-10-05 15:00")            # from the .ics, not from the prose

    def test_without_an_invitation_a_job_kind_is_unchanged(self):
        prose = "Please complete the assessment by Oct 6, 2099 12:46 PM ET"
        with_none = mail.make_item("me@x.com", head("Assessment"), prose, "oa", "", None)
        self.assertTrue(with_none["due"].startswith("2099-10-06"))

    def test_cancellations_and_replies_are_not_events(self):
        self.assertIsNone(mail.first_event(ics("DTSTART:20991005T150000", "SUMMARY:x", method="CANCEL")))
        self.assertIsNone(mail.first_event(ics("DTSTART:20991005T150000", "SUMMARY:x", "STATUS:CANCELLED")))
        self.assertIsNone(mail.first_event(ics("DTSTART:20991005T150000", "SUMMARY:x", method="REPLY")))
        self.assertIsNone(mail.first_event(""))


class CalendarPart(unittest.TestCase):
    def build(self, ctype, filename=None):
        m = EmailMessage()
        m["Subject"] = "Invitation"
        m.set_content("plain text body")
        maintype, subtype = ctype.split("/")
        kw = {"filename": filename} if filename else {}
        m.add_attachment(INVITE.encode(), maintype=maintype, subtype=subtype, **kw)
        return m

    def test_text_calendar(self):
        self.assertIn("BEGIN:VEVENT", mail.calendar_text(self.build("text/calendar")))

    def test_application_ics(self):
        self.assertIn("BEGIN:VEVENT", mail.calendar_text(self.build("application/ics")))

    def test_attachment_named_ics(self):
        self.assertIn("BEGIN:VEVENT", mail.calendar_text(self.build("application/octet-stream", "invite.ics")))

    def test_a_message_without_one(self):
        m = EmailMessage()
        m.set_content("no calendar here")
        self.assertEqual(mail.calendar_text(m), "")


class EventsAsDeadlines(SliceTest):
    def rows(self, items):
        return self.json_out("""
            import json
            from inbox import save_inbox, load_inbox
            from deadlines import all_deadlines, load_deadlines
            box = load_inbox(); box["items"] = %s; save_inbox(box)
            out = [(d["name"], d["at"], d["source"]) for d in all_deadlines(load_deadlines(), load_inbox())]
            print(json.dumps(out))
        """ % json.dumps(items))

    def item(self, **kw):
        base = {"id": "e1", "key": "k1", "kind": "event", "status": "open", "title": "Career fair",
                "due": "2099-10-05 15:00", "detail": "Cohon Center", "date": "2099-10-01", "company": "CPDC"}
        base.update(kw)
        return base

    def test_an_upcoming_event_shows_as_a_deadline(self):
        rows = self.rows([self.item()])
        self.assertEqual(rows, [["Career fair", "2099-10-05 15:00", "inbox"]])

    def test_past_and_hidden_events_do_not(self):
        rows = self.rows([self.item(id="a", due="2020-01-01 09:00"),
                          self.item(id="b", status="hidden"),
                          self.item(id="c", due="")])
        self.assertEqual(rows, [])

    def test_events_sit_alongside_assessments(self):
        rows = self.rows([self.item(),
                          {"id": "o1", "key": "k2", "kind": "oa", "status": "open", "company": "Acme",
                           "platform": "HackerRank", "due": "2099-10-02 11:00", "detail": "60 min",
                           "date": "2099-10-01", "title": "OA"}])
        self.assertEqual([r[0] for r in rows], ["Acme HackerRank", "Career fair"])   # soonest first


if __name__ == "__main__":
    unittest.main()
