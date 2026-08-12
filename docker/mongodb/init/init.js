// MongoDB initialization script for automation bots

// Create databases for each bot
db = db.getSiblingDB('linkedin_it_db');
db.createUser({
  user: 'linkedin_it_user',
  pwd: 'changeme',
  roles: [
    {
      role: 'readWrite',
      db: 'linkedin_it_db'
    }
  ]
});

db = db.getSiblingDB('linkedin_general_db');
db.createUser({
  user: 'linkedin_general_user',
  pwd: 'changeme',
  roles: [
    {
      role: 'readWrite',
      db: 'linkedin_general_db'
    }
  ]
});

db = db.getSiblingDB('indeed_it_db');
db.createUser({
  user: 'indeed_it_user',
  pwd: 'changeme',
  roles: [
    {
      role: 'readWrite',
      db: 'indeed_it_db'
    }
  ]
});

db = db.getSiblingDB('indeed_general_db');
db.createUser({
  user: 'indeed_general_user',
  pwd: 'changeme',
  roles: [
    {
      role: 'readWrite',
      db: 'indeed_general_db'
    }
  ]
});

db = db.getSiblingDB('glassdoor_it_db');
db.createUser({
  user: 'glassdoor_it_user',
  pwd: 'changeme',
  roles: [
    {
      role: 'readWrite',
      db: 'glassdoor_it_db'
    }
  ]
});

db = db.getSiblingDB('glassdoor_general_db');
db.createUser({
  user: 'glassdoor_general_user',
  pwd: 'changeme',
  roles: [
    {
      role: 'readWrite',
      db: 'glassdoor_general_db'
    }
  ]
});

db = db.getSiblingDB('automation_monitoring');
db.createUser({
  user: 'monitoring_user',
  pwd: 'changeme',
  roles: [
    {
      role: 'readWrite',
      db: 'automation_monitoring'
    }
  ]
});

print('MongoDB databases and users created successfully!');
