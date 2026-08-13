"""Personal profile data used to fill application forms (IT profile template)."""
import os

# Legal name
first_name = os.getenv("CANDIDATE_FIRST_NAME", "Jane")
middle_name = os.getenv("CANDIDATE_MIDDLE_NAME", "")
last_name = os.getenv("CANDIDATE_LAST_NAME", "Doe")

# Phone number (required), make sure it is valid with country code
phone_number = os.getenv("CANDIDATE_PHONE", "+1-555-0199")
email_address = os.getenv("CANDIDATE_EMAIL", "jane.doe@example.com")

# Current city
current_city = os.getenv("CANDIDATE_CITY", "Surrey")

# Address
street = os.getenv("CANDIDATE_STREET", "100 Main Street")
state = os.getenv("CANDIDATE_STATE", "BC")
zipcode = os.getenv("CANDIDATE_ZIPCODE", "V6B 1A1")
country = os.getenv("CANDIDATE_COUNTRY", "Canada")

## Equal Opportunity / Voluntary Self-Identification questions
ethnicity = os.getenv("CANDIDATE_ETHNICITY", "Decline")
gender = os.getenv("CANDIDATE_GENDER", "Male")
pronouns = os.getenv("CANDIDATE_PRONOUNS", "He/Him/His")
disability_status = os.getenv("CANDIDATE_DISABILITY", "Decline")
veteran_status = os.getenv("CANDIDATE_VETERAN", "Decline")
