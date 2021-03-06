# This module serves a Flask app asynchronously using Celery.
# This video is very helpful in understanding this: https://www.youtube.com/watch?v=iwxzilyxTbQ
# To get this running follow these steps:
# 1: Open a terminal window
# 2: If you are using the PythonData environment, enter the following command to activate it:
#    $ source activate PythonData
# 3. Navigate into the project folder
# 4. Enter the following command to start the Celery worker and beat:
#    $ celery -A app.celery worker --loglevel=info 
# 5: Open a second terminal window
# 6. Navigate into the project folder
# 7: If you are using the PythonData environment, enter the following command to activate it:
#    $ source activate PythonData
# 8: Enter the following command to start the Flask app:
#    $ python3 app.py

# Todo: Can we create a boot script that will do the above whenever our virtual machine boots?

# Here we have a Flask app and a Celery worker in the same file.
# The two processes have access to the variable names defined globally here
# However, they do not share memory, so they are using two different copies of those variables!

# Todo: Find a way to simplify the above architecture.
# Todo: Create a readme file for this project
# Todo: Consider applying OOP principles
# Todo: Could skills change after being passed into a celery function?

from flask import Flask, request, render_template, jsonify
from flask_cors import CORS
import boto3
from flask_celery import make_celery
from urllib.parse import parse_qs, urlencode
import datetime
import pandas as pd
from dynamodb_json import json_util as json

from scrape import get_job_links_page, get_job
from analysis import analyze, reanalyze

dynamodb = boto3.resource('dynamodb', region_name='us-east-2')
skill_table = dynamodb.Table('Skills')
constraints_table = dynamodb.Table('Constraints')
jobs_table = dynamodb.Table('Jobs')
jobids_table = dynamodb.Table('JobIds')
analysis_table = dynamodb.Table('Analysis')

MAX_PAGES_PER_QUERY = 7

flask_app = Flask(__name__)
CORS(flask_app)
flask_app.config.update(
    CELERY_BROKER_URL='redis://project2-2.7xt2wp.ng.0001.use2.cache.amazonaws.com:6379',
    CELERY_RESULT_BACKEND='redis://project2-2.7xt2wp.ng.0001.use2.cache.amazonaws.com:6379'
)
celery = make_celery(flask_app)

@flask_app.route('/')
def hello():
    # return 'Hello, World!'
    return render_template('index.html', name=None)

@flask_app.route("/delete-skill/<skill>")
def delete_skill(skill):
    global skills
    try:
        del skills[skill]
    except:
        pass
    skill_table.delete_item(Key={'skill_name': skill})
    jobs = jobids_table.scan()['Items']
    
    for job in jobs:
        print (job['JobId'])
        print (skill)
        analysis_table.update_item(
            Key={'JobId': job['JobId']},
            UpdateExpression='remove ' + skill)
    return "OK"

@flask_app.route('/show-skills/')
def show_skills():
    return render_template('skills.html', name=None)

def put_skill(skill, have):
    global skills   # As a reminder, don't do this in a celery function, but only in flask functions.
    
    sk = {"skill_name": skill, "have": have}
    # print ("Before append:")
    # print (skills)
    skills = skills.append(sk, ignore_index=True)
    # print ("After append:")
    # print (skills)
    skill_table.put_item(Item=sk)
    return 'Inserted: ' + skill

def add_skill_I_have(skill):
    r = put_skill(skill, True)
    # Todo: How long does reanalyzing take?  Should it be handled by celery? 
    # A: It depends on how many jobs you have scraped so far.  Ultimately, yes, it should be handled by celery.
    # print ("Calling reanalyze")
    reanalyze(skill, jobs_table, analysis_table)
    scrape.delay(skill, skills.to_json())
    return r
    
# The return value for these routes is not being used for anything.  Feel free to change it to something useful
@flask_app.route("/do-have/<skill>")
def do_have(skill):
    r = add_skill_I_have(skill)    
    return r

@flask_app.route("/dont-have/<skill>")
def dont_have(skill):
    reanalyze(skill, jobs_table, analysis_table)
    return put_skill(skill, False)

# This route is used to capture the search constraints
@flask_app.route("/jobs/")
def jobs():
    constraints = {k: v for k,v in request.args.items()}
    skill = constraints.pop('q')
    r = add_skill_I_have(skill)    
    # after popping q from a, the remaining parameters are the constraints
    # Todo: Also need to strip the start page
    encoded = urlencode(constraints)
    c={'ConstraintId': 1, 'Constraint': encoded}
    constraints_table.put_item(Item=c)
    return ("OK")

# Todo: Negate the scrore for skills where "have" is false
def sort_jobs(df):
    skill_dict = {}
    skills = skill_table.scan()['Items']
    for s in skills:
        skill_dict[s['skill_name']] = s['have']
    if 'Score' in df.columns:
        df.drop('Score', axis=1)
    for col in df.columns:
        if not col=='JobId':
            if not skill_dict[col]:
                df[col] = df[col].apply(lambda x: 0 - x)
    df['Score'] = df.sum(axis = 1)
    print (df['Score'])
    return df.sort_values('Score', ascending=False)

@flask_app.route("/get-top-jobs/")
def get_top_jobs():
    # now analysis_df is local
    analysis_df = pd.DataFrame(json.loads(analysis_table.scan()['Items'])).fillna(False)
    analysis_df = sort_jobs(analysis_df)
    top_jobs_df = pd.DataFrame()
    for row in range(0,10):
        if row >= len(analysis_df):
            break
        # print ("Score: " + str(analysis_df.iloc[row]['Score']))
        JobId = analysis_df.iloc[row]['JobId']
        response = jobs_table.get_item(Key={'JobId': JobId})
        # print (JobId)
        job = response['Item']
        # print(response)
        # print(job)
        job_row = pd.DataFrame([job], index=[JobId])
        top_jobs_df = top_jobs_df.append(job_row)
    return top_jobs_df.to_json()

@flask_app.route("/get-top-skills/")
def get_top_skills():
    # global analysis_df
    # now analysis_df is local
    analysis_df = pd.DataFrame(json.loads(analysis_table.scan()['Items'])).fillna(False)
    analysis_df = sort_jobs(analysis_df)
    trimmed_df = analysis_df.copy()
    if 'JobId' in trimmed_df.columns:
        trimmed_df = trimmed_df.drop('JobId', axis=1)
    for index, row in skills.iterrows():
        if not row['have']:
            # print("Dropping " + row['skill_name'])
            if row['skill_name'] in trimmed_df:
                trimmed_df = trimmed_df.drop(row['skill_name'], axis=1)
    skill_scores = 3 * trimmed_df.iloc[0:10].sum(axis=0)
    skill_scores += 2 * trimmed_df.iloc[10:20].sum(axis=0)
    skill_scores += 1 * trimmed_df.iloc[20:30].sum(axis=0)
    return skill_scores.to_json()
    
@flask_app.route("/get-neg-skills/")
def get_neg_skills():
    analysis_df = pd.DataFrame(json.loads(analysis_table.scan()['Items'])).fillna(False)
    analysis_df = sort_jobs(analysis_df)
    trimmed_df = analysis_df.copy()
    if 'JobId' in trimmed_df.columns:
        trimmed_df = trimmed_df.drop('JobId', axis=1)
    for index, row in skills.iterrows():
        if row['have']:
            # print("Dropping " + row['skill_name'])
            if row['skill_name'] in trimmed_df:
                trimmed_df = trimmed_df.drop(row['skill_name'], axis=1)
    skill_scores = 3 * trimmed_df.iloc[0:10].sum(axis=0)
    skill_scores += 2 * trimmed_df.iloc[10:20].sum(axis=0)
    skill_scores += 1 * trimmed_df.iloc[20:30].sum(axis=0)
    return skill_scores.to_json()
    
    
@flask_app.route("/get-skills/")
def get_skills():
    response = skill_table.scan()
    skills = response['Items']
    # print(skills)
    return jsonify(skills)

# Scrape data about one job from Indeed
@celery.task(name='app.scrape_job')
def scrape_job(id, link, json_skills):
    skills = pd.read_json(json_skills)  # Pandas dataframes can't be passed directly into celery tasks
    j = get_job(link)
    j['JobId'] = id
    j['link'] = link
    jobs_table.put_item(Item=j)
    # jobs_table_queue.append(j)
    # print("Passing job to analyze")
    d = analyze(j, skills, analysis_table)

    # if len(d.keys()) == len(d.values()):
    # analysis_df.loc[id] = d
    return d
    # return pd.DataFrame(d, index=[id])

# Scrape a list of job links by searching Indeed
@celery.task(name='app.scrape')
def scrape(query, json_skills):
    print("Commencing scrape")

    response = constraints_table.get_item(Key={'ConstraintId': 1})
    constraints = response['Item']['Constraint']
    for page in range(1, MAX_PAGES_PER_QUERY+1):
        (links, found_jobs, ids) = get_job_links_page(query, constraints, page)
        zipped = list(zip(ids, links))
        jobs = [{"JobId": i, "link": l} for i,l in zipped]
        with jobids_table.batch_writer() as batch:
            for j in jobs:
                batch.put_item(Item={'JobId': j['JobId']})
        with jobs_table.batch_writer() as batch:
            for j in jobs:
                batch.put_item(Item=j)
        for i,l in zipped:
            d = scrape_job.delay(i, l, json_skills)
            # Todo: Improve efficiency by gathering these dictionaries into a list and then appending to the dataframe as a batch
            # scraped_analysis = scraped_analysis.append(pd.DataFrame(d, index=[i]))
        if found_jobs > 0 and found_jobs < (page*10):
            break

    print ("Found: " + str(found_jobs) + " jobs")
    now = datetime.datetime.now().isoformat()
    sk = {"skill_name": query,
            "have": True,
            "last_searched": now}
    # Todo: Consider doing this earlier
    skill_table.put_item(Item=sk)
    # return scraped_analysis


# analysis_df = pd.DataFrame(json.loads(analysis_table.scan()['Items'])).fillna(False)
# skill_dict = {}
# skills = skill_table.scan()['Items']
# for s in skills:
#     skill_dict[s['skill_name']] = s['have']
# for col in analysis_df.columns:
#     print(skill_dict.get(col))
#     if not skill_dict[col]:
#         print("Not")


# skills is a Flask variable
# Todo: Please explain who is using it
skills = pd.DataFrame(json.loads(skill_table.scan()['Items'])).fillna(False)
# Todo: Make skill_name the index
# print(skills)
# if not skills.empty:
#     for skill in skills.get('skill_name'):
#         if skill not in analysis_df.columns:
#             analysis_df[skill] = pd.Series([0] * len(analysis_df), index=analysis_df.index)

# AWS limits the DynamoDB throughput.
# At one point we were using a celery beat to meter the transfer of scraped jobs data into the database.
# Jobs waited in memory in jobs_table_queue
# However, this ended up being too complicated, because:
# When the user enters a new skill, we have to reanalyze the jobs already scraped to scan them for this new skill
# The ones that are already in the database have to be read out.
# The reading is also throughput limited, so it should also be done by the beat.
# The jobs in the queue to be written also need to be reanalyzed.
# The flask app doesn't have direct access to the namespace of celery's variables, so the reanalysis of these jobs needs to be done by celery as well.
# The new skill name to be reanalyzed needs to be passed from the Flask app to Celery.
# Flask sends data to Celery by calling a celery function.
# Celery functions don't execute immediately, they are deferred, by design.
# So when Flask receives a new skill and tries to send it to Celery, Celery will meanwhile be pushing jobs, that need to be reanalyzed, from its buffer into DynamoDB,
# only to be read back out again for reanalysis.  We don't know how to issue a priority interruption of this process for purposes of informing Celery of the new skill.
# So at that point we gave up on the whole idea of metering our DynamoDB throughtput.
# We solved it instead by paying AWS a small amount of money for more throughput capacity.

# The reason for this celery beat is to avoid overloading the limited throughput capacity of our Amazon DynamoDB.
# @celery.on_after_configure.connect
# def setup_periodic_tasks(sender, **kwargs):
#     sender.add_periodic_task(CELERY_BEAT_PERIOD, put_job.s(), name='beat for put_job')

# jobs_table_queue is for use by Celery and only Celery.
# scrape_job() appends to it and put_job() pops from it.
# jobs_table_queue = []  # This list temporarily holds scraped job data in RAM until put_job() gets around to putting it into DynamoDB

# CELERY_BEAT_PERIOD = 3.0
# PUT_JOB_BATCH_SIZE = 10

# putting_jobs = False
# #Put job data into the jobs table of the database
# @celery.task
# def put_job():
#     # Don't put any jobs into the database while we're reanalyzing
#     print (len(jobs_table_queue))

#     if len(jobs_table_queue) > 0:
#         batch_count = 0;
#         with jobs_table.batch_writer() as batch:
#             while len(jobs_table_queue) > 0 and batch_count < PUT_JOB_BATCH_SIZE:
#                 j = jobs_table_queue.pop()
#                 if (j):
#                     batch.put_item(Item = j)
#                     batch_count += 1

if __name__ == "__main__":
    flask_app.run(host="0.0.0.0", port=8080, ssl_context='adhoc')

    