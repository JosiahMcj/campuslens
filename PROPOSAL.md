# Golden Eagle AI Cabinet — Project Proposal for Executive SIS Intelligence

*Prepared for university leadership and the Gloo AI Hackathon team. Added to the repo
2026-09-24 as the source of scope; `ROADMAP.md` turns it into dated work.*

## Project Identity

Golden Eagle AI Cabinet is a governed team of permission-limited AI employees that helps
university leaders turn Student Information System data into clear executive understanding
and timely human action.

**Tagline:** Governed AI employees helping university leaders turn SIS data into
human-centered action.

## First Use Case

The two-week prototype will deliver a President Weekly Student Success Briefing. A
university leader asks one question in ordinary language: "What should I know about spring
registration?" The AI Cabinet analyzes synthetic Ellucian-style data, produces an
evidence-based briefing, identifies actions for staff, and separates operational
recommendations from decisions that require leadership.

## Proposal Decision

Approve a tightly scoped hackathon prototype with three AI employees: an AI Chief of Staff,
an Enrollment Analyst, and a Student Success Analyst. The prototype will use fictional
student records and will not modify an SIS, send communications, or make decisions about
individual students.

## Expected Value

| Current Problem | Proposed Improvement |
|---|---|
| Executive questions require several departments and multiple reports. | One request produces a consolidated briefing with evidence and responsible owners. |
| Dashboards show metrics without explaining why they changed. | AI analysts connect approved measures and summarize likely contributing factors. |
| Leaders may not know which issue requires their decision. | The briefing separates staff actions from leadership decisions. |
| Sensitive data may be shared more broadly than needed. | Each AI employee receives only the fields required for its assigned role. |

## Problem and Opportunity

### The Leadership Problem

University leaders oversee enrollment, student success, finance, academics, and
operations, but the evidence needed for a decision is distributed across systems and
offices. Ellucian may contain the underlying student record, yet senior leaders usually
rely on prepared reports and departmental interpretation. The process can be slow,
repetitive, and difficult to audit. A leader may receive several accurate reports without
receiving one clear account of what changed, who is affected, what staff are doing, and
what decision remains.

### The Product Opportunity

Golden Eagle AI Cabinet treats institutional analysis as coordinated staff work. A Chief of
Staff agent receives an executive question, assigns narrow analytical tasks, and combines
the results. Specialist agents work within defined permissions and return structured
findings with source fields. The system presents proposed actions but leaves decisions and
communications to authorized people.

### Why AI Employees Instead of One Chatbot

| Single General Chatbot | Golden Eagle AI Cabinet |
|---|---|
| May receive more data than the question requires | Routes each task to a role with limited data access |
| Produces one answer without clear analytical ownership | Shows which AI employee produced each finding |
| May mix facts, interpretation, and recommendation | Separates verified metrics, analysis, proposed actions, and decisions |
| Provides limited accountability after the response | Records requests, data access, drafts, approvals, and refusals |

### Executive Questions the Platform Can Eventually Support

- How is enrollment tracking against the same point last year?
- Which student groups face the largest registration barriers?
- Which programs are growing or declining?
- Where are unresolved holds affecting continued enrollment?
- Which findings require operational follow-up and which require leadership action?

The prototype will answer only the spring-registration question. Additional questions
belong to the product roadmap, not the two-week build.

## AI Cabinet Structure

| AI Employee | Role in the Prototype | Data Boundary |
|---|---|---|
| AI Chief of Staff | Interprets the executive question, assigns tasks, combines findings, and prepares the briefing | Receives aggregated findings and only the detail required to support the final briefing |
| Enrollment Analyst | Compares registration, enrollment, program, and student-level measures with a historical baseline | Reads enrollment and program fields; does not read counseling or spiritual-care records |
| Student Success Analyst | Identifies common administrative and advising barriers among students who have not registered | Reads registration status, holds, advising indicators, and approved risk fields |

### Human Roles

| Human Role | Responsibility |
|---|---|
| President or Executive | Reviews the briefing and makes leadership decisions |
| Data Owner | Approves definitions, fields, and access for each measure |
| Student Success Leader | Validates recommended operational follow-up |
| Institutional Research or Analyst | Checks calculations, comparisons, and interpretation |
| Information Security and Privacy | Approves access, retention, audit, and pilot controls |

### Operating Principle

AI employees analyze, explain, and draft. Authorized university leaders decide. The
prototype must make this distinction visible in the interface and in the event log.

## First Use Case: President Weekly Student Success Briefing

**Executive Question:** What should I know about spring registration?

### Illustrative Briefing

The demonstration may use fictional results such as the following. These values are demo
data, not university findings.

| Briefing Section | Illustrative Content |
|---|---|
| What happened | Spring registration is 4.8 percent below the same point in the prior year. |
| Who is affected | Most of the difference is concentrated among 42 continuing students who have not registered. |
| Why it matters | Registration closes soon enough that targeted human outreach may still resolve common barriers. |
| Contributing factors | Eighteen fictional students have financial holds below $1,000, and twelve have no recorded advising appointment this term. |
| Staff action | Ask Student Success to review students without advising contact and ask Financial Aid to review the small-balance cases. |
| Leadership decision | Decide whether to authorize a focused emergency-aid eligibility review for students below a defined balance threshold. |

### Executive Briefing Format

1. Executive summary in five sentences or fewer.
2. Current measure and historical comparison.
3. Student groups most affected.
4. Evidence and source fields behind every major claim.
5. Operational actions with a responsible office.
6. Leadership decisions stated separately.
7. Known limitations, missing data, or conflicting definitions.

### Four Minute Demonstration

| Time | Demonstration | Purpose |
|---|---|---|
| 0:00 to 0:30 | Introduce the leadership problem and the spring-registration question. | Establish a clear executive need. |
| 0:30 to 1:10 | Show the Chief of Staff assigning work to the two analysts. | Demonstrate coordinated AI employees. |
| 1:10 to 2:20 | Present the executive briefing and its proposed actions. | Show useful synthesis rather than a generic chat response. |
| 2:20 to 3:10 | Open one claim and inspect its source fields and calculation. | Demonstrate explainability and SIS grounding. |
| 3:10 to 3:40 | Record a leadership decision as an approved follow-up task. | Keep authority with a human. |
| 3:40 to 4:00 | Show the audit log and a denied data request. | Demonstrate governance and permission boundaries. |

## Christian University Design Principles

The Christian identity of the product should appear in how it treats people, authority,
truth, and care. It should not depend on adding religious language to an executive
dashboard or allowing AI to make spiritual judgments.

| Principle | Product Requirement |
|---|---|
| Human dignity | Describe students as people who may need support, not as risk scores or financial units. |
| Truthfulness | Show sources, state uncertainty, and refuse to invent missing facts. |
| Human responsibility | Require authorized people to approve decisions and consequential actions. |
| Stewardship | Help leaders use existing university resources and staff attention more effectively. |
| Care and relationship | Measure whether students received timely human support, not only whether an institutional metric improved. |
| Freedom and privacy | Do not infer spiritual condition or expose the content of counseling, prayer, or chaplain conversations. |

### Whole Person Executive View

A future version may summarize academic, financial, relational, vocational, and voluntary
care outcomes at an aggregate level. Sensitive spiritual-care content should remain outside
the executive briefing. Leadership may see that students requested and received care
connections, but not the private content of those conversations.

## Two Week Minimum Viable Product

### Required Features

1. An executive dashboard with the spring-registration briefing.
2. A natural-language question field restricted to the approved use case.
3. A fictional Ellucian-style dataset containing 100 to 200 student records.
4. Three visible AI employees with distinct responsibilities.
5. Verified calculations for registration status and historical comparison.
6. A findings view that links each claim to its source fields.
7. An action plan that assigns follow-up to an office.
8. A separate leadership-decision section.
9. An audit log showing tasks, data access, results, approval, and refusal.
10. One demonstration of an AI employee refusing access outside its role.

### Currently Out of Scope

- Production Ellucian access or real student data
- Automatic changes to student records
- Automatic email, text message, or case creation
- An unrestricted executive chatbot
- Predictive retention modeling
- Financial-aid eligibility decisions
- Tutor, international-student, or spiritual-care agents
- A complete mobile application

### Definition of Done

The prototype is complete when we can run the full demonstration in four minutes,
every reported metric matches the fictional source data, every major statement exposes its
evidence, the AI Cabinet produces one coherent briefing, a human approves the leadership
follow-up, and one unauthorized request is visibly refused and recorded.

## Data and Technical Approach

| Layer | Hackathon Approach | Future Pilot |
|---|---|---|
| User interface | Executive dashboard, briefing, evidence view, decision panel, and audit log | Approved leadership application or university portal |
| Data | Synthetic Ellucian-style JSON records | Approved Ellucian Ethos or institutional integration layer |
| Metrics | Deterministic calculations in code | Governed institutional definitions and validated reporting logic |
| AI coordination | Chief of Staff routes two structured analytical tasks | Role-based orchestration with monitoring and evaluation |
| AI analysis | Grounded summaries of verified measures | Approved models, policies, and institutional knowledge |
| Actions | Creates a simulated follow-up task after executive approval | Approved workflow integration with audit controls |
| Security | No production student data | Least-privilege access, retention rules, FERPA review, and audit |

### Suggested Fictional Data Fields

| Area | Example Fields |
|---|---|
| Student profile | Pseudonymous ID, program, class level, continuing or new student |
| Enrollment | Term, registration status, registered credit hours, registration date |
| Administrative status | Hold category, responsible office, hold date, resolved status |
| Advising | Assigned advisor, last appointment date, appointment status |
| Comparison | Equivalent date in prior year, prior-term status, institutional baseline |

## Two Week Delivery Plan

| Timing | Primary Work | Deliverable |
|---|---|---|
| Days 1 and 2 | Freeze the executive question, metrics, briefing format, fictional schema, and demo script. | Approved scope and storyboard |
| Days 3 and 4 | Generate fictional records, implement calculations, and build the dashboard shell. | Verified data service and interface |
| Days 5 and 6 | Build the Enrollment Analyst and evidence links. | Enrollment findings with source support |
| Days 7 and 8 | Build the Student Success Analyst and barrier analysis. | Student-success findings with source support |
| Day 9 | Build the Chief of Staff routing and consolidated briefing. | Complete AI Cabinet response |
| Day 10 | Add action assignment, executive decision, and audit log. | Human approval workflow |
| Day 11 | Add permission boundaries and one refusal demonstration. | Governance demonstration |
| Days 12 to 14 | Validate calculations, test edge cases, improve design, record a backup demo, and rehearse. | Competition-ready submission |

## Team Responsibilities

| Team Member | Primary Responsibility |
|---|---|
| Product Lead | Own executive use case, Ellucian context, metric definitions, Christian design principles, scope, and pitch |
| Vibe Coding Developer | Build dashboard, briefing, evidence, decision, and audit-log experiences |
| Computer Science Student | Build fictional data, API, deterministic calculations, agent routing, permissions, and tests |

## Immediate Team Decisions

1. Confirm the spring-registration question as the only prototype use case.
2. Select the five to seven metrics that belong in the executive briefing.
3. Define every metric in writing before implementing the AI explanation.
4. Approve the fictional data schema and expected demonstration results.
5. Assign one owner for product, interface, and data logic.
6. Freeze the four-minute script before adding optional features.

## Risks and Controls

| Risk | Control |
|---|---|
| The project becomes a general executive chatbot | Restrict the prototype to one approved question and one briefing template. |
| AI invents a number or causal claim | Calculate all measures in code; the model may explain verified results but may not create metrics. |
| Sensitive detail reaches senior leaders unnecessarily | Default to aggregate results and reveal student-level fictional records only in the controlled evidence demonstration. |
| AI employees receive excessive access | Assign fields by role and record each access; demonstrate a refusal. |
| The system takes action without authorization | Create only a proposed task and require executive approval. |
| Christian framing becomes surveillance or judgment | Use whole-person care principles without inferring faith, character, or private spiritual condition. |
| We exceed our two-week capacity | Keep three AI employees, one dataset, one question, and one four-minute workflow. |

## Measures of Success

| Hackathon Evidence | Future Pilot Measure |
|---|---|
| Briefing generated in one workflow | Time required to prepare the weekly leadership briefing |
| Every key claim exposes evidence | Analyst correction rate and executive trust rating |
| All metrics match source data | Accuracy against governed institutional reports |
| Action and decision are clearly separated | Percentage of recommendations routed to the correct owner |
| Permission refusal is recorded | Unauthorized-access tests passed |
| Four-minute demonstration is stable | Leadership adoption and recurring use |

## Competition Pitch

**One sentence:** Golden Eagle AI Cabinet transforms Ellucian SIS data into an executive
briefing by coordinating permission-limited AI employees across enrollment and student
success, helping university leaders see what matters, understand why, and direct timely
human action.

**Thirty second pitch:** University leaders have extensive student data, but answering one
important question can still require several departments and multiple reports. Golden
Eagle AI Cabinet gives each analytical function a permission-limited AI employee. An AI
Chief of Staff coordinates their work, verifies the evidence, and produces one executive
briefing that separates staff actions from leadership decisions. Our first workflow answers
the practical question of what the president should know about spring registration. The result
is faster institutional understanding while people remain responsible for every
consequential decision.

## Product Roadmap

| Phase | Capability |
|---|---|
| Hackathon | President Weekly Student Success Briefing with Enrollment and Student Success analysts |
| Pilot | Approved SIS connector, governed metric definitions, and a limited executive user group |
| Expansion | Advisor, financial-support, international-student, and academic-program analysis seats |
| Long Term | A governed university AI operating model with reusable employees, permissions, and audit |

## Approval Requested

Approve Golden Eagle AI Cabinet as the project name and approve the President Weekly
Student Success Briefing as the two-week prototype. We should not add another AI
employee or another executive question until the complete briefing, evidence, approval, and
audit workflow operates reliably.
