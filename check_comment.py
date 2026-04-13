import os
from github import Github
from github import Auth

g = Github(auth=Auth.Token(os.environ["GITHUB_TOKEN"]))
repo = g.get_repo("globalpocket/brownie-sampleproject")
issue = repo.get_issue(2)
comment = issue.get_comment(4234404991)
print(f"Comment ID: {comment.id}")
print(f"Created at: {comment.created_at}")
print(f"Updated at: {comment.updated_at}")
print(f"Body: {comment.body}")
