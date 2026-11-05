# Enactus UTAS MIS

Members, meetings and attendance, projects with documents, reports, and sign-in by password or Google.

## Run it
    python -m venv venv
    venv\Scripts\activate            (Mac/Linux: source venv/bin/activate)
    pip install -r requirements.txt
    python -m flask --app app create-admin     (first time only)
    python app.py                              -> http://127.0.0.1:5000

## Upgrading from the earlier version
Copy your existing `mis.db` into this folder before starting. The app upgrades it automatically and keeps all members, meetings, attendance and projects.

## Portfolios and who can change what
President and Vice President run everything. Their own changes apply at once, and they approve everyone else's.
| Portfolio | May change |
|---|---|
| Secretary | Attendance (the only role that records it), member records and documentation |
| P.R.O | Scheduling meetings, announcements |
| Project Manager | Projects, budgets, teams, documents, and who may edit each project |
| Finance Director | Dues and other payments, receipts |
| Member | Sees own attendance and all projects. Can edit a project only if the Project Manager cleared them for it |

Every change by anyone other than the President or Vice President waits in Approvals until BOTH approve it (set APPROVAL_MODE=either in .env to accept one). One rejection cancels it. Recording a new payment is the exception: it takes effect at once so the receipt can go out, and editing or voiding it later needs approval.
Giving a member project clearance is immediate, but their edits still need approval.

## Receipt emails
Fill the SMTP lines in `.env` (see `.env.example`). The Finance Director records a payment and the receipt is emailed to the member's address. If email is not set up or fails, the payment is still saved and "Send email receipt" can be pressed later.

## Upgrading
Old roles convert automatically: Admin becomes President, and Executive becomes Member. Reassign portfolios under Members > Edit.

## Access levels (old)
- Member: own attendance, projects, meetings list.
- Executive: members, meetings, attendance, projects, documents, reports, exports.
- Admin (President): all of the above plus logins, reset links, deactivation, activity log.

## Attendance
Present, Late, Excused or Absent. Present and Late count as attended. Excused is left out of the percentage, so it does not count against anyone.

## Forgot password (self-service)
On the login page, "Forgot your password?" emails a link (valid 1 hour, single use) to the member's registered address. It uses the same email settings as receipts, so fill in the SMTP lines in `.env` first. Without email, the page tells people to ask the President or Vice President for a reset link instead (below).
When the system is live, also set `BASE_URL` in `.env` (for example https://mis.yourclub.org) so reset links always point at your real site. In production the self-service reset stays off until BASE_URL is set.
The page never reveals whether an email is registered, and requests are rate limited.

## Units (IT, Field Agents, Research...)
President and Vice President create units under Units and tick who belongs to each (or tick units on a member's edit page). A person can be in several. The Secretary can view units. Units show on the Members list, in the attendance filter, in the members export, and as attendance rates on Reports.
The "Academic department" field on a member is separate (their course department).

## Forgotten passwords (admin-created link)
Admin opens the member > Edit > "Create reset link", then sends the link privately. It works once, for 24 hours.

## Google sign-in (optional)
Only people already added to the Members list can sign in, using the same email as their Google account.
1. Go to console.cloud.google.com, create a project, then APIs & Services > OAuth consent screen (External) and fill the basics.
2. Credentials > Create credentials > OAuth client ID > Web application.
3. Authorised redirect URI: `http://127.0.0.1:5000/auth/google/callback` (for local use). When you go live, add `https://your-domain/auth/google/callback` too.
4. Copy `.env.example` to `.env` and paste the Client ID and Client secret.
5. Restart the app. A "Sign in with Google" button appears on the login page.
The `.env` file holds secrets: never share it or upload it anywhere.

## Going live
    set SECRET_KEY=<long random text>   and   set PRODUCTION=1      (Mac/Linux: export)
    gunicorn -w 2 -b 127.0.0.1:8000 app:app
Run it behind HTTPS (Nginx or Caddy with a free certificate); Google sign-in also needs an https address when not on localhost.

## Back up
Run `python3 backup.py` (or schedule it daily). It makes a safe dated copy of `mis.db` and a zip of `uploads` in the `backups` folder, keeping the newest 14. Download a recent copy to a personal computer weekly, and practise restoring once.

## Back up (manual)
Copy `mis.db` and the `uploads` folder regularly. Handle member data in line with Ghana's Data Protection Act, 2012 (Act 843).

## Credit and ownership
"System by ..." appears in the sidebar, on the login page, on receipts and in receipt emails. Change the name by adding `BUILT_BY=Your Name` to `.env` (no code change), then restart or Reload. Copyright notices are in the code files and LICENSE.txt (a draft: confirm ownership with the club).
