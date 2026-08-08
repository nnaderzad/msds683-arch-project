Description
Group Project: Designing a Data Architecture
Overview
Through this group project, you will apply the concepts covered in class to design a complete, end-to-end data architecture for a domain of your choice. Groups are 3 students each.
Learning Goals
Through completing this project, make sure you can:
    • translate a real-world domain into a structured data model
    • Make and defend schema design decisions (star vs. snowflake, OLAP vs. OLTP)
    • Design and implement at least one cloud ETL process 
    • Orchestrate your pipeline using Airflow
    • Communicate your design clearly to a technical audience
Picking a domain: 
Business goal:
Your architecture exists to support analytical and “business” use cases. You must identify a broad goal and 3-4 specific analytical use cases relevant to that goal that can be answered using data.  Examples from last year  

Technical depth:
Pick a domain that satisfies 1-3 of these characteristics:
    • Multi-source or multi-format data
        ◦ Different sources, data providers, or APIs, etc. 
        ◦ structured tables, semi-structured files (JSON, Parquet), and unstructured content (text documents, logs, images, or video)
    • Highly scalable (100 GB to TBs) 
        ◦ Either handle such a large dataset or be ready to make and defend decisions that will work at that scale. 
    • Governance and Security
        ◦ Highly sensitive domains — healthcare, finance, legal, and education.
        ◦ Governance constraints drive your architecture decisions – access control, auditing, anonymization, etc.
    • Temporal Richness
        ◦ Data changes and accumulates meaningfully over time – and you must show this in your project. 
        ◦ Example: event streams, daily transactions, sensor readings, etc. 



Sources: You are encouraged to use publicly available datasets, but in many domains, you may not find public datasets that satisfy all your constraints. Start from a real dataset, but be prepared to augment it with synthetic data. 
Milestones
Milestone (total 100 pts)	Details	Submission
M1 — Domain & Dataset (10 pts)	Align on a domain and identify your data sources. 
Include: team name (after a food item), member names, chosen domain, analytical question(s), and dataset sources.	Upload your Google Slides link to Canvas. 
Use this same deck for all milestones — do not create a new deck.
M2 — Domain Model (10 pts)	Apply domain modeling to your domain. Include an ER diagram with at least 5–6 entities and relationship annotations. Identify your data needs from the ER diagram and compare them with your data sources. Make an initial guess about which entities become fact vs dimensions	No separate submission. Add slides to your existing deck – will review in class.
M3 — Midterm Design Pitch (20 pts)	~7 min group presentation. See the Midterm design pitch tab for details.	No separate submission. (hide any slides you are not presenting, but keep everything in one deck) 
M4 — GitHub Repo (10 pts)	Submit your GitHub repo link + checklist of everything that’s on GitHub. 	No separate submission - add a slide to your deck on how I can access the GitHub repo + checklist 
Final Presentation & Demo (50 pts)	~10 min group presentation with a working demo. See the Final presentation tab for details.	No separate submission. (hide anything you are not presenting, do not delete) 
Also see the separate tab on the “Bonus” component (+20). 
A Note on the Lakehouse Course
For MSDS 681 (Data Lakehouse) next term, this architecture project will serve as the design foundation for the lakehouse you build there. You are not required to continue the same project, but starting with a solid architecture design will be a real advantage.
Bonus
Bonus: Implementation of a Data Quality or Infrastructure Tool (+20 pts)
Demonstrated at the Final Presentation
For full bonus credit, implement one of the following in your project pipeline:
    • Great Expectations — define and run data quality validations (at least one) in your pipeline. Show the expectation definitions and a validation result.
    • Terraform — provision cloud resources used in your project using a Terraform configuration (at least one resource). Show the .tf files and a successful Terraform apply.
You will earn the bonus for only one tool. Bonus work must be demonstrated live or shown clearly in your final presentation slides.
Midterm design pitch
Midterm Design Pitch
Pressure-test your design decisions before final implementation 
Grading will be based on the presentation of ideas and the clarity of communication — it is ok if you are still figuring out technical things.
Time: ~ 7 minutes + QnA (each group). 
Scope:
    1. Introduce your domain and business goal (~1min)
    2. Processing Paradigm and Schema Design (~2 min) OLAP or OLTP? Star or snowflake schema or something else? Draw your schema: Identify your fact table(s) and dimension tables. Explain briefly why you chose this schema for your use case.
    3. Transformation Pipeline (2-3 min) Draw a diagram showing the flow from raw source data through to your proposed output tables. Clearly label each step with what the transformation does — for example: clean nulls and standardize date formats → join orders with customers → aggregate to daily revenue summary → load into fact_daily_sales. You do not need to have built this yet; you are proposing it. You may also propose a potential use case or end use from this pipeline, but its ok if that’s still a work in progress. 
    4. Early Tech Stack/Diagram (~1 min): What tools and services are you planning to use? This can be a simple list or a diagram showing compute, storage, orchestration tools, etc.
QnA: Your group will be asked at least two questions – one by me and one by your peers. 
 
Final presentation
Final Presentation & Demo
Time: ~ 10 minutes
Your presentation must cover:
    1. Domain & Business Question (~1 min) Quick recap for context. This should be tight — your audience already knows your domain from the midterm design pitch.
    2. Schema Design (~2 min) Present your final schema. Note any changes you made since the mid-term design pitch and explain why.
    3. ETL Process — Demo (~4 min) Walk through at least one ETL pipeline end-to-end. Show the raw source data, the transformation logic, and the resulting output table. The transformation should be meaningful — cleaning, joining, aggregating, or enriching data, not just moving it.
    4. (Maybe) Orchestration — Demo (~3 min) Show your pipeline running in an orchestration tool (Airflow or equivalent). A DAG screenshot is acceptable if a live demo is not feasible, but walk through what each task does and how dependencies are defined.
    5. Tech Stack (1-2 min) Present a diagram of your full technology stack. For each component, briefly explain what role it plays and why you chose it over alternatives.
    6. Budget & Cost Model (1-2 min): Estimate the monthly and yearly cost of running your system in production. Break it down by component — storage, compute, orchestration, etc. State your assumptions clearly (data volume, query frequency, team size).
    7. (+1-2 min for bonus)
Grading breakdown for Final presentation + demo:
Component	Points
Schema design is clearly explained and justified	10
Transformations are defined by business logic and at least one transformation is demo’ed	15
Proper GitHub use (all code changes and scripts are tracked) 	10
Tech stack is appropriately justified	5
Budget model is provided — realistic estimates with stated assumptions	5
Presentation clarity and demo quality	15
Total	60

Open public datasets
Random collections
    1. https://www.kaggle.com/datasets
    2. https://github.com/awesomedata/awesome-public-datasets
    3. https://datahub.io/collections
    4. https://datasetsearch.research.google.com/ (it's Google search specifically for datasets)
    5. https://registry.opendata.aws/ (Amazon’s collection of open datasets)

YouTube (video data)
    1. https://research.google.com/youtube8m/

Non-profit/social/government:
    1. https://data.worldbank.org/
    2. https://data.gov/

public opinion/media/sports: 
    1. https://data.fivethirtyeight.com/

Social media:
    1. https://snap.stanford.edu/data/


