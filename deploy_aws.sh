#!/bin/bash

# AWS Deployment Script for Job Automation Bots
# This script handles the complete deployment to AWS

set -e

echo "🚀 Starting AWS Deployment for Job Automation Bots..."

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to print colored output
print_status() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

print_step() {
    echo -e "${BLUE}[STEP]${NC} $1"
}

# Check if we're in the right directory
if [ ! -f "requirements.txt" ]; then
    print_error "requirements.txt not found. Please run from the automation root directory."
    exit 1
fi

# Step 1: Get user's public IP
print_step "Getting your public IP address..."
PUBLIC_IP=$(curl -s ifconfig.me)
print_status "Your public IP: $PUBLIC_IP"

# Classify IPv4 vs IPv6
ALLOWED_RDP_IP_V4=""
ALLOWED_RDP_IP_V6=""
if [[ "$PUBLIC_IP" =~ : ]]; then
    ALLOWED_RDP_IP_V6="${PUBLIC_IP}/128"
    print_status "Detected IPv6 address: $ALLOWED_RDP_IP_V6"
else
    ALLOWED_RDP_IP_V4="${PUBLIC_IP}/32"
    print_status "Detected IPv4 address: $ALLOWED_RDP_IP_V4"
fi

# Step 2: Check if Terraform is initialized
print_step "Checking Terraform setup..."
cd terraform

if [ ! -d ".terraform" ]; then
    print_status "Initializing Terraform..."
    terraform init
else
    print_status "Terraform already initialized."
fi

# Step 3: Create terraform.tfvars file
print_step "Creating Terraform variables file..."
if [ -z "$TF_VM_ADMIN_PASSWORD" ]; then
    print_error "TF_VM_ADMIN_PASSWORD env var is not set. Aborting."
    exit 1
fi
cat > terraform.tfvars << EOF
aws_region        = "${AWS_DEFAULT_REGION:-us-west-2}"
vm_name           = "jobbots-dev-vm"
instance_type     = "t3.large"
admin_password    = "$TF_VM_ADMIN_PASSWORD"
allowed_rdp_ip_v4 = "$ALLOWED_RDP_IP_V4"
allowed_rdp_ip_v6 = "$ALLOWED_RDP_IP_V6"
golden_image_id   = "${GOLDEN_IMAGE_ID:-}"
EOF

print_status "Created terraform.tfvars"

# Step 4: Deploy infrastructure
print_step "Deploying AWS infrastructure..."
print_warning "This will take a few minutes..."

terraform plan -out=tfplan
terraform apply tfplan

# Step 5: Get VM details
print_step "Getting VM connection details..."
VM_IP=$(terraform output -raw vm_public_ip)
VM_NAME=$(terraform output -raw vm_name)
VM_ID=$(terraform output -raw vm_id)
RDP_STRING=$(terraform output -raw rdp_connection_string)

print_status "VM deployed successfully!"
print_status "VM IP: $VM_IP"
print_status "VM Name: $VM_NAME"
print_status "VM Instance ID: $VM_ID"
print_status "RDP Connection: $RDP_STRING"

# Step 6: Create deployment package
print_step "Creating deployment package..."
cd ..

# Create a zip file with all necessary files
DEPLOY_PACKAGE="automation_deploy_$(date +%Y%m%d_%H%M%S).zip"

# Files to include
FILES_TO_INCLUDE=(
    "requirements.txt"
    "automation_monorepo/"
    "master/Auto_job_applier_linkedIn_gen/"
    "master/Auto_job_applier_linkedIn_it/"
    "master/gen_indeed/"
    "master/it_indeed cwgeopy/"
    "training_data_corpus/"
    "sync_core.sh"
    ".env.example"
    "ixbrowser_session.zip"
)

# Create the package
zip -r "$DEPLOY_PACKAGE" "${FILES_TO_INCLUDE[@]}"

print_status "Created deployment package: $DEPLOY_PACKAGE"

# Step 7: Generate setup instructions
print_step "Generating setup instructions..."

cat > AWS_SETUP_INSTRUCTIONS.md << EOF
# AWS EC2 VM Setup Instructions

## VM Connection Details
- **IP Address**: $VM_IP
- **Instance ID**: $VM_ID
- **Username**: Administrator
- **RDP Command**: $RDP_STRING

## Setup Steps

### 1. Connect via RDP
1. Open Remote Desktop Connection
2. Enter: $VM_IP
3. Username: Administrator
4. Password: (use TF_VM_ADMIN_PASSWORD env var)

### 2. Deploy Project Files
1. Copy the deployment package: $DEPLOY_PACKAGE
2. Extract to C:\automation\
3. Update configuration files with VM IP: $VM_IP

### 3. Install Python Dependencies
\`\`\`powershell
cd C:\automation
python -m pip install -r requirements.txt
playwright install chromium
\`\`\`

### 4. Update Configuration
Edit these files to replace localhost with $VM_IP:
- automation_monorepo/config/settings.py
- All bot config/*/secrets.py files
- All bot config/*/bot.yaml files

### 5. Test Services
\`\`\`powershell
# Test MongoDB
mongosh --eval "db.adminCommand('ping')"

# Test automation
cd C:\automation\automation_monorepo
python supervisor.py --list
python supervisor.py --include-not-ok --once
\`\`\`

## Service URLs
- **MongoDB**: mongodb://$VM_IP:27017
- **Chrome CDP**: http://$VM_IP:9222-9228

## Security Notes
- MongoDB is accessible from your IP only
- RDP is accessible from your IP only
- Change the default password after first login

Generated: $(date)
EOF

print_status "Generated setup instructions: AWS_SETUP_INSTRUCTIONS.md"

# Step 8: Summary
echo ""
print_status "🎉 AWS deployment completed!"
echo ""
echo "📋 Next Steps:"
echo "1. Connect via RDP: $RDP_STRING"
echo "2. Deploy project files from: $DEPLOY_PACKAGE"
echo "3. Update configuration files with VM IP: $VM_IP"
echo "4. Test all services and run the bots"
echo ""
echo "📄 Full instructions saved to: AWS_SETUP_INSTRUCTIONS.md"
echo "📦 Deployment package: $DEPLOY_PACKAGE"
echo ""
print_warning "Remember to:"
echo "- Update all configuration files with the VM IP address"
echo "- Test each service before running the automation bots"
echo ""
