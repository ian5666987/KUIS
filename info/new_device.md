Moving a Django Project to a New Windows Device

This procedure assumes:

The Django project folder has already been copied to the new computer.
The old computer's venv was copied along with it, but will not be reused.
The old project uses Python 3.14.3.
The new computer uses Python 3.14.7.
PostgreSQL and pgAdmin are already installed on the new computer.
The old database data is not required; only the database structure/model needs to be recreated.
The project uses PostgreSQL.


---------------------------------------------------
1. Open the copied project in VS Code

On the new computer:

VS Code → File → Open Folder

Select the copied Django project folder.

Open:

Terminal → New Terminal


---------------------------------------------------
2. Check that you are in the project folder

In the VS Code terminal:

dir


Make sure you can see:

manage.py


The terminal must be in the directory containing manage.py.


---------------------------------------------------
3. Delete the old copied virtual environment

The copied project may contain:

venv/


Delete this folder.

Do not reuse the old computer's virtual environment on the new computer.

Your project should temporarily look something like:

my_project/
├── manage.py
├── my_project/
├── app1/
├── app2/
└── ...


---------------------------------------------------
4. Install Python on the new computer

The old computer used:

Python 3.14.3


Python 3.14.7 was installed on the new computer instead.

This is generally fine because both are Python 3.14.

During Windows installation, make sure:

☑ Add python.exe to PATH


is enabled.


---------------------------------------------------
5. Verify Python

Close/reopen VS Code if necessary, open a new terminal, and run:

python --version


Expected:

Python 3.14.7


---------------------------------------------------
6. Create a new virtual environment

From the directory containing manage.py:

python -m venv venv


This creates a new environment specifically for the new computer.

The project now looks like:

my_project/
├── manage.py
├── venv/
├── my_project/
├── app1/
└── ...


---------------------------------------------------
7. Activate the new virtual environment

Run:

venv\Scripts\Activate.ps1


The terminal should now start with:

(venv)


For example:

(venv) PS C:\Projects\my_project>


---------------------------------------------------
8. Verify the new virtual environment

Run:

python --version


Expected:

Python 3.14.7


Then:

where.exe python


The result should point to something like:

C:\...\my_project\venv\Scripts\python.exe


This confirms that the project is using the new virtual environment.


---------------------------------------------------
9. Check for requirements.txt

Initially, the old project did not have one.

Since the old computer's environment was working correctly, use it to generate one.

On the old computer, activate the old venv and run:

pip freeze


This shows the packages installed in the old environment.


---------------------------------------------------
10. Create requirements.txt on the old computer

On the old computer, while the old venv is activated:

pip freeze > requirements.txt


This creates:

requirements.txt


containing the packages and versions from the working old environment.

In this case, the file contained:

asgiref==3.11.1
Django==6.0.4
psycopg2-binary==2.9.12
sqlparse==0.5.5
tzdata==2026.1


---------------------------------------------------
11. Copy requirements.txt to the new computer

Copy the newly created:

requirements.txt


from the old project to the new project.

The new project should now look approximately like:

my_project/
├── manage.py
├── requirements.txt
├── venv/
├── my_project/
├── app1/
└── app2/


The important distinction is:

requirements.txt → copy it
Old venv → do not reuse it
New venv → keep the new one


---------------------------------------------------
12. Install the project dependencies

On the new computer, make sure the new venv is activated:

(venv)


Then run:

pip install -r requirements.txt


This installs the required Django/Python packages into the new virtual environment.


---------------------------------------------------
13. Verify Django

Run:

python -m django --version


The expected result is:

6.0.4


---------------------------------------------------
14. Check the Django project

Run:

python manage.py check


If everything is correctly installed, you should see:

System check identified no issues (0 silenced).


At this point, the Django/Python environment is working correctly.

PostgreSQL Setup

The project uses PostgreSQL, so the new computer needs a database corresponding to the configuration in settings.py.

The project's database configuration is:

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': 'corpus_db',
        'USER': 'corpus_user',
        'PASSWORD': 'mypassword',
        'HOST': 'localhost',
        'PORT': 5432,
    }
}


Because the old data is not required, there is no need to copy/restore the old PostgreSQL database.

Instead, create a new empty database and let Django recreate the tables through migrations.


---------------------------------------------------
15. Open pgAdmin on the new computer

Open pgAdmin.

Find your PostgreSQL server under:

Servers
└── PostgreSQL ...


Connect to the server using the PostgreSQL administrator password that was configured when PostgreSQL was installed on the new computer.


---------------------------------------------------
16. Create the corpus_user PostgreSQL user

In pgAdmin:

Servers
└── PostgreSQL ...
    └── Login/Group Roles


Right-click:

Login/Group Roles


and select:

Create → Login/Group Role


Set:

Name: corpus_user


Under the password/definition section, set:

Password: mypassword


Make sure the user is allowed to log in.

Save the role.

Note

The PostgreSQL administrator password and the corpus_user password are two separate things.


---------------------------------------------------
17. Create the corpus_db database

In pgAdmin:

Servers
└── PostgreSQL ...
    └── Databases


Right-click:

Databases


and select:

Create → Database...


Set:

Database: corpus_db
Owner: corpus_user


Then save.

You should now have:

Databases
└── corpus_db


---------------------------------------------------
18. Run Django migrations

Return to the VS Code terminal on the new computer.

Make sure the new venv is activated:

(venv)


Then run:

python manage.py migrate


Django will connect to:

localhost:5432
        ↓
corpus_db
        ↓
corpus_user


and use the project's migration files to create the database tables.

You should see messages similar to:

Applying ... OK


The exact migrations depend on the project.

Important

You do not need to manually create all the tables in pgAdmin.

Django's migration files define the database structure.


---------------------------------------------------
19. Create a new Django administrator account if needed

Because this is a new, empty database, the old users/data will not exist.

If the project uses Django's admin site, create a new administrator:

python manage.py createsuperuser


Follow the prompts to create the new username, email, and password.


---------------------------------------------------
20. Start the Django server

Finally:

python manage.py runserver


You should see something like:

Starting development server at http://127.0.0.1:8000/


Open this in your browser:

http://127.0.0.1:8000/


Your Django project should now be running on the new computer.

Final Setup Structure

After everything is complete, the new computer should roughly have:

my_project/
│
├── manage.py
├── requirements.txt
├── venv/
│   ├── Scripts/
│   └── ...
│
├── my_project/
│   ├── settings.py
│   ├── urls.py
│   └── ...
│
├── app1/
│   ├── migrations/
│   └── ...
│
└── app2/
    ├── migrations/
    └── ...


And PostgreSQL should have:

PostgreSQL
│
├── User: corpus_user
│
└── Database: corpus_db
    │
    ├── Django tables
    ├── app tables
    └── ...


The key principle is:

OLD COMPUTER
    │
    ├── Project files ──────────────→ NEW COMPUTER
    │
    ├── requirements.txt ──────────→ NEW COMPUTER
    │
    └── old venv ────────X─────────→ DO NOT REUSE
                                     
NEW COMPUTER
    │
    ├── Install Python
    ├── Create NEW venv
    ├── Install requirements.txt
    ├── Create PostgreSQL user
    ├── Create PostgreSQL database
    ├── Run Django migrations
    └── Run Django server


The old PostgreSQL data is not transferred in this approach. The new database starts empty, while Django migrations recreate the database structure.