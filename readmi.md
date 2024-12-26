## Prerequisites
- Python 3.8 or higher
- AWS Account with access credentials
- pip (Python package manager)

## NOTE: install AWSCLI and configure your aws credentials

## Installation

1. Clone the repository:
   git clone 

2. Create a virtual environment:
    python3 -m venv venv

3. Activate the virtual environment:
    Windows: venv\Scripts\activate

    Unix/MacOS: source venv/bin/activate

4. Install required packages:
    pip install -r requirements.txt

5. create infrastructure
    cd backend
    python infrastructure.py

6. run application 
    cd ../frontend
    python app.py