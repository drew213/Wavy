"""The rest of the operating cadence, registered as contracts without handlers.

The scheduler skips a definition with no handler, so none of these runs yet. They are here so the
whole loop is visible in one registry: what triggers each job, what it checks, what it does, which
role owns it and how we know it's done. Implementing one means writing its handler and passing it
to register() in place of None.
"""
from __future__ import annotations

from ..jobs import REGISTRY, JobDefinition, register

CONTRACTS = [
    JobDefinition(
        "post_send.review", "Post-send review", "event", "analyst", area="reporting", events=("campaign.sent",),
        checks="Once a campaign has had time to land: deliverability (bounces, spam complaints, unsubscribes), "
               "opens, clicks, orders and revenue, against this client's recent sends and the campaign's own goal.",
        action="Writes a short read of how the send did and why, and opens an insight for anything off pattern "
               "(a deliverability spike, a broken link, a segment that didn't respond).",
        completion="Done when the review is attached to the campaign and every problem it found is an insight "
                   "with evidence, or it says the send was clean."),
    JobDefinition(
        "weekly.trends", "Weekly trends", "weekly", "analyst", area="reporting",
        checks="The week against the prior four: revenue by channel, flow and campaign revenue, list growth, "
               "deliverability, and the flows that carry the most revenue.",
        action="Turns each real movement into an insight with the numbers behind it; ignores noise inside "
               "normal week-to-week swing.",
        completion="Done when every metric was compared and each meaningful change is an insight, or it says "
                   "nothing moved beyond normal."),
    JobDefinition(
        "weekly.calendar_readiness", "Calendar readiness", "weekly", "strategist", area="campaign_calendar",
        checks="Every send planned for the next 14 days: does it have an approved brief, copy, design, audience "
               "and offer, and is it built and scheduled in the platform?",
        action="Opens a task for each missing piece with its owner and the date it's needed by, and flags any "
               "send that can't make its date.",
        completion="Done when every upcoming send is either ready or has open tasks covering what's missing."),
    JobDefinition(
        "biweekly.meeting_prep", "Client meeting prep", "biweekly", "account_lead", area="client_meetings",
        checks="Everything since the last meeting: results measured, decisions waiting on the client, open "
               "insights, what shipped and what slipped.",
        action="Drafts the agenda and the decisions we need from the client, with the evidence for each, for "
               "the account lead to review.",
        completion="Done when the draft agenda is ready for the account lead at least one working day before the meeting."),
    JobDefinition(
        "monthly.report", "Monthly report", "monthly", "analyst", area="reporting",
        checks="The closed month: revenue and attributed revenue by channel, flow and campaign performance, list "
               "and deliverability health, results measured this month, against last month and last year.",
        action="Drafts the monthly report with the strategist's notes left for a person to write or approve.",
        completion="Done when the draft report is attached for review; it goes to the client only through an approval."),
    JobDefinition(
        "monthly.calendar", "Next month's calendar", "monthly", "strategist", area="campaign_calendar",
        checks="Next month's dates, launches, inventory, promotions already agreed, and what worked and didn't "
               "in the learning log.",
        action="Drafts next month's campaign calendar as a decision for the client to approve, with the brief for each send.",
        completion="Done when the calendar is proposed and waiting on approval at least two weeks before the month starts."),
    JobDefinition(
        "quarterly.roadmap", "Quarterly roadmap", "quarterly", "account_lead", area="quarterly_planning",
        checks="The quarter's results against its goals, the open insights, the learning log and the client's "
               "plans for the next quarter.",
        action="Drafts the growth roadmap for next quarter: the few bets worth making, what each should move, "
               "and how we'll measure it.",
        completion="Done when the roadmap is proposed as decisions for the client to approve."),
    JobDefinition(
        "event.anomaly", "Metric anomaly", "event", "analyst", area="reporting", events=("metric.anomaly",),
        checks="The metric that tripped: is the move real (enough volume, outside normal range), and what "
               "changed around it (a flow, a send, the site, tracking)?",
        action="Opens one insight with the evidence and the likely cause, or closes it out as noise with the reason.",
        completion="Done when the anomaly is an insight someone owns, or dismissed with a reason."),
    JobDefinition(
        "event.tracking_failure", "Tracking failure", "event", "analyst", area="data_infrastructure",
        events=("tracking.failed",),
        checks="Which events stopped or broke (Placed Order, Started Checkout, Viewed Product...), since when, "
               "and which flows and reports depend on them.",
        action="Opens a critical insight naming what's broken and what it affects, and a task to get it fixed "
               "with the client's developer.",
        completion="Done when the failure is an insight with an owner and the affected reports are marked "
                   "unreliable for that period."),
    JobDefinition(
        "event.client_feedback", "Client feedback", "event", "account_lead", area="account_management",
        events=("client.feedback",),
        checks="What the client said: is it a request, a correction to what we know about them, approval or "
               "rejection of work, or a concern about the relationship?",
        action="Routes it: a correction becomes a proposed client fact, a request becomes an insight or task, a "
               "concern goes to the account lead the same day.",
        completion="Done when the feedback is acknowledged and recorded where it belongs, and the client has "
                   "heard back within one working day."),
]

for _defn in CONTRACTS:
    if _defn.key not in REGISTRY:
        register(_defn)
