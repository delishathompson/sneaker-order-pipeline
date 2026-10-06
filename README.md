# Case study: Automated Order Intake & Fulfillment Pipeline

**Status: fully built and tested end to end** - intake, approval,
categorization, pricing, customer notification, and a live production
tracker dashboard are all working together as one automated system.

---

## Overview

A serverless order-intake, approval, and pricing system built for Deli
Bespoke, a custom handcrafted sneaker brand. A customer submits a build
request through a web form; the system automatically saves it, notifies
the shop owner for approval, categorizes the design, calculates a price
estimate, emails the customer their quote, and tracks the order through
production on a live dashboard - replacing what was previously manual,
ad-hoc order tracking across texts and DMs.

---

## The problem

Deli Bespoke takes custom sneaker orders that previously had to be
tracked by hand - no structured way to capture order details, no
consistent approval step before committing to a build, and no
standardized pricing process. This made it hard to:
- Keep track of every request and its status
- Apply consistent, sustainable pricing across orders
- Separate "orders I've committed to" from "requests I'm still
  reviewing"
- Quote prices confidently rather than improvising a number per order

---

## The solution

An automated pipeline that takes an order from a public web form all the
way through approval, categorization, price estimation, and production
tracking - without manual data entry at any stage.

****

### How it works
1. **Intake** - a customer fills out a web form (pattern, size, color/
   inspiration, or a free-text custom request if their size/pattern
   isn't listed). A Lambda function validates the submission and writes
   it to DynamoDB with status `pending_approval`.
2. **Approval** - the shop owner gets an email notification (via SNS)
   with the full order details and one-click approve/reject links.
   Nothing moves forward without this manual checkpoint.
3. **Categorization** - once approved, a Lambda function automatically
   reads the order's pattern and notes to classify it by design category
   (streetwear, sports tribute, etc.) and flags any complexity add-ons
   (rush, custom sole, extra colorway) based on keywords in the
   customer's own description.
4. **Pricing** - a price estimate is calculated automatically from a
   base price (by pattern) plus any detected add-on surcharges, pulled
   from a pricing table rather than hardcoded - so updating prices never
   requires touching code.
5. **Customer notification** - once priced, the customer automatically
   receives an email (via SES) with their order summary and price
   estimate - no manual quoting required.
6. **Production tracking** - a live dashboard shows every order sorted
   into columns by status (pending approval → approved → categorized →
   priced → cutting → painting → sealing → shipped), with manual
   "advance to next stage" buttons for the physical build steps.

---

## Tech stack

- **AWS Lambda** (Python) - order validation, approval handling,
  categorization logic, pricing calculation, customer email, dashboard
  data
- **API Gateway** (HTTP API) - public endpoints for the order form and
  dashboard
- **DynamoDB** - order storage/status tracking, plus a separate pricing
  table so prices can be updated without a code change
- **Amazon S3** - static hosting for the order form and dashboard
- **Amazon SNS** - shop-owner approval notifications
- **Amazon SES** - customer-facing price estimate emails
- **HTML/CSS/JavaScript** - the customer-facing order form and the
  internal production dashboard

---

## My role

Designed and built the entire pipeline solo — from data model design
(what fields an order needs, how status moves through stages) through
the AWS infrastructure, every Lambda function's business logic, the
front-end order form, and the production-tracking dashboard.

### Development approach

I used AI tools (Claude) to help draft code and documentation, and I
cross-referenced the official AWS documentation to confirm services,
permissions, and configuration. I deployed everything myself in AWS,
tested it, debugged issues, and corrected the output as I went. The
project ideas and the problems they solve come from my own day-to-day
work.

---

## Challenges and what I learned

This is genuinely one of the most useful sections of this case study -
almost nothing here worked on the first try, and most of the real
learning happened in the debugging, not the initial build.

- **Region mismatches** - AWS resources are region-scoped; a Lambda, its
  DynamoDB table, and its API Gateway all need to live in the same
  region to talk to each other. Standardized everything on `us-east-1`
  after hitting this early.
- **CORS debugging** - spent real time distinguishing a genuine CORS
  configuration issue from a CORS error message that was actually
  masking a completely different root cause underneath. Learned to
  check CloudWatch logs directly rather than trusting the browser's
  error message alone - a CORS error is often the symptom, not the
  disease.
- **Local file testing limitations** - opening an HTML file directly
  from disk (`file://`) creates a browser security context that behaves
  differently than a real hosted page. Solved by hosting the form on S3
  instead of testing locally.
- **One integration, many routes** - the single biggest debugging
  lesson of the project. API Gateway's console allowed only one Lambda
  integration to exist easily, and every route had been silently
  sharing it - so "fixing" one route's target function would
  unknowingly break another route that depended on the same shared
  integration. Diagnosed by comparing the full Integrations list
  against the full Routes list side by side, rather than troubleshooting
  one route in isolation. Resolved by explicitly creating a separate
  integration per function and deleting the leftover duplicates.
- **IAM permissions aren't inherited** - a function that could read/
  write its own data still couldn't invoke *another* Lambda function,
  or send email via SES, without that specific permission being
  explicitly attached to its own role. Each new function needed its own
  permissions checked individually, every time.
- **Un-deployed placeholder code** - more than once, an API call
  returned a successful-looking response that was actually just
  Lambda's default "Hello from Lambda!" starter code, because the real
  logic had been pasted in but never deployed. Learned to verify actual
  response *content*, not just status codes, especially right after
  creating a new function.
- **Environment variables need as much care as code** - a malformed SNS
  ARN (invisible whitespace from a copy-paste), a value pasted into the
  wrong function's environment variables entirely, and a missing
  trailing-slash bug in a constructed URL all cost real debugging time.
  Typing values manually, or using a service's built-in copy icon rather
  than manual text selection, avoided repeat versions of this same
  class of bug.

---

## Results

The full pipeline has been tested end to end with multiple real test
orders, confirming:
- Orders submitted through the form reliably reach DynamoDB
- The approval email and one-click approve/reject flow works correctly
- Approval automatically and correctly triggers categorization, which
  automatically triggers pricing - no manual intervention between steps
- Price estimates calculate correctly, including add-on surcharges
  (e.g., a "rush" order correctly returns a higher price than a
  standard order)
- Customers receive an automated price-estimate email
- The dashboard correctly sorts every order into its live status column

---

## What I'd improve next

- Scope IAM permissions down from broad managed policies
  (`AmazonDynamoDBFullAccess`, `AWSLambda_FullAccess`) to
  least-privilege, resource-specific policies for production use
- Add a customer-facing order-status lookup page
- Move from rule-based categorization to a trained model once there's
  enough order history to learn from
- Add true HTTPS (via CloudFront) in front of the S3-hosted form before
  sharing it publicly with customers
- Request SES production access to allow emailing real customers
  without individual address verification

---

## Links
*[Add once available]*
- Live form: [link]

- Demo video walkthrough: [link]
