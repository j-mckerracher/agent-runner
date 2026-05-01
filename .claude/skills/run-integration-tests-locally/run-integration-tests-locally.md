---
name: run-integration-tests-locally
description: 'Guide for running integration tests locally against a locally-running instance of the RLS Orders Consumer API. This skill provides step-by-step instructions for setting up and executing integration tests with full debugging capabilities.'
---

# Run Integration Tests Locally

## Overview

A specialized skill for running integration tests locally against a locally-hosted instance of the RLS Orders Consumer API. This allows you to test the full request/response cycle, including real interactions with external services, while having the ability to set breakpoints and debug test scenarios in your IDE.

## When to Use This Skill
- When you need to run end-to-end integration tests during local development
- When debugging API behavior with integration test scenarios
- When validating changes against real external service integrations
- When you want to test the full request/response cycle locally

## Setup Steps

### 1. Start the API Locally

Navigate to the API project and run it on port 50000:

```bash
cd src/Mayo.MCS.RLS.OrdersConsumer.Api
dotnet run --urls "http://localhost:50000"
```

**Alternative methods:**
- Use `dotnet run` (will use default ports from launchSettings.json)
- Run via IDE (F5 in Rider/Visual Studio)

**Verify the API is running:**
- Open browser to `http://localhost:50000/swagger`
- Or check health: `curl http://localhost:50000/health`

### 2. Run Integration Tests

Open a **new terminal window** (keep API running) and execute tests:

```bash
cd src && dotnet test Mayo.MCS.RLS.OrdersConsumer.Tests.Integration/Mayo.MCS.RLS.OrdersConsumer.Tests.Integration.csproj --settings Mayo.MCS.RLS.OrdersConsumer.Tests.Integration/local.runsettings
```

## Common Commands

### Run All Integration Tests
```bash
cd src && dotnet test Mayo.MCS.RLS.OrdersConsumer.Tests.Integration/Mayo.MCS.RLS.OrdersConsumer.Tests.Integration.csproj --settings Mayo.MCS.RLS.OrdersConsumer.Tests.Integration/local.runsettings
```

### Run Specific Test Class
```bash
cd src && dotnet test Mayo.MCS.RLS.OrdersConsumer.Tests.Integration/Mayo.MCS.RLS.OrdersConsumer.Tests.Integration.csproj --settings Mayo.MCS.RLS.OrdersConsumer.Tests.Integration/local.runsettings --filter "FullyQualifiedName~OrdersPostTests"
```

### Run Single Test Method
```bash
cd src && dotnet test Mayo.MCS.RLS.OrdersConsumer.Tests.Integration/Mayo.MCS.RLS.OrdersConsumer.Tests.Integration.csproj --settings Mayo.MCS.RLS.OrdersConsumer.Tests.Integration/local.runsettings --filter "FullyQualifiedName=Mayo.MCS.RLS.OrdersConsumer.Tests.Integration.Features.Orders.OrdersPostTests.CreateOrder_WithSpecimens"
```

### Run Tests by Feature Area
```bash
# Orders tests
cd src && dotnet test Mayo.MCS.RLS.OrdersConsumer.Tests.Integration/Mayo.MCS.RLS.OrdersConsumer.Tests.Integration.csproj --settings Mayo.MCS.RLS.OrdersConsumer.Tests.Integration/local.runsettings --filter "FullyQualifiedName~Features.Orders"

# Labels tests
cd src && dotnet test Mayo.MCS.RLS.OrdersConsumer.Tests.Integration/Mayo.MCS.RLS.OrdersConsumer.Tests.Integration.csproj --settings Mayo.MCS.RLS.OrdersConsumer.Tests.Integration/local.runsettings --filter "FullyQualifiedName~Features.Labels"

# Documents tests
cd src && dotnet test Mayo.MCS.RLS.OrdersConsumer.Tests.Integration/Mayo.MCS.RLS.OrdersConsumer.Tests.Integration.csproj --settings Mayo.MCS.RLS.OrdersConsumer.Tests.Integration/local.runsettings --filter "FullyQualifiedName~Features.Documents"

# Reports tests
cd src && dotnet test Mayo.MCS.RLS.OrdersConsumer.Tests.Integration/Mayo.MCS.RLS.OrdersConsumer.Tests.Integration.csproj --settings Mayo.MCS.RLS.OrdersConsumer.Tests.Integration/local.runsettings --filter "FullyQualifiedName~Features.Reports"
```

### Run Tests by Category
```bash
cd src && dotnet test Mayo.MCS.RLS.OrdersConsumer.Tests.Integration/Mayo.MCS.RLS.OrdersConsumer.Tests.Integration.csproj --settings Mayo.MCS.RLS.OrdersConsumer.Tests.Integration/local.runsettings --filter "TestCategory=FunctionalityTests"
```

### Run with Verbose Output
```bash
cd src && dotnet test Mayo.MCS.RLS.OrdersConsumer.Tests.Integration/Mayo.MCS.RLS.OrdersConsumer.Tests.Integration.csproj --settings Mayo.MCS.RLS.OrdersConsumer.Tests.Integration/local.runsettings --verbosity detailed
```

### Run with Code Coverage
```bash
cd src && dotnet test Mayo.MCS.RLS.OrdersConsumer.Tests.Integration/Mayo.MCS.RLS.OrdersConsumer.Tests.Integration.csproj --settings Mayo.MCS.RLS.OrdersConsumer.Tests.Integration/local.runsettings --collect:"XPlat Code Coverage"
```
Coverage reports will be in `src/TestResults/`

## Test Structure

### Test Organization
```
Mayo.MCS.RLS.OrdersConsumer.Tests.Integration/
├── Features/
│   ├── Accounts/           # Account API tests
│   ├── Documents/          # Document generation tests
│   ├── Labels/             # Label generation tests
│   ├── Orders/             # Order CRUD and search tests
│   ├── Reports/            # Report retrieval tests
│   └── Tests/              # Test catalog tests
├── _Helpers/               # Test utilities and builders
├── Resources/              # Test resources (PDFs, etc.)
└── local.runsettings       # Local test configuration
```

### Test Framework Details
- **Test Runner**: MSTest
- **HTTP Client**: Mayo.ODN.Test.E2E (`BaseTestingFramework`)
- **Test Data Builders**: Custom builder classes for creating test data
- **Authentication**: Azure AD (configured in local.runsettings)

## Configuration

### local.runsettings Key Parameters
| Parameter | Value | Purpose |
|-----------|-------|---------|
| `BaseUrl` | `http://localhost:50000/` | Local API endpoint |
| `UseAzureADAuth` | `True` | Enable Azure AD authentication |
| `ClientId` | `d749f21a-0d4e-4287-bb1b-539496ab85b7` | Azure AD app registration |
| `ClientSecret` | `<REDACTED>` | Azure AD secret |
| `ResourceId` | `https://nonprod-pearls-api.mayo.edu` | Token audience |
| `OrdersOrchBaseUrl` | `https://order-orch-us-central1-...` | Orders Orchestration API |
| `env` | `dev` | Environment flag |

Test data parameters (pre-existing data in dev environment):
- `PatientId`: `P90009000`
- `ClientAccountNumber`: `7042553`
- `ClientOrderNumber`: `90000900034`
- `SpecimenNumber`: `00001121999`
- `MayoAccessNumber`: `Q100-90000900036`
- `PearlsOrderNumber`: `D90000900036`
- `TestId`: `FPEPA`

## View Logs
- API console output shows Serilog messages
- `DetailedErrorMessages: "Enabled"` in `appsettings.Development.json`
- Use `--verbosity detailed` flag for test output

## Troubleshooting

### Tests fail with 401 Unauthorized
**Cause**: Azure AD authentication failed

**Solutions**:
- Verify Mayo VPN is connected
- Check Azure AD credentials in `local.runsettings` are valid
- Verify `ResourceId` and `AzureAdAuthority` are correct

### Tests fail with connection errors
**Cause**: Cannot reach API endpoint

**Solutions**:
- Ensure API is running: `curl http://localhost:50000/health`
- Check port in `local.runsettings` matches API port
- Verify firewall isn't blocking connections

### API won't start locally
**Cause**: Configuration or dependency issues

**Solutions**:
1. Verify .NET 8.0 SDK: `dotnet --version`
2. Restore packages: `cd src && dotnet restore`
3. Check port conflicts: `lsof -i :50000` (macOS) or `netstat -ano | findstr :50000` (Windows)
4. Review console output for errors

### External service integration tests fail
**Cause**: Cannot reach external APIs

**Solutions**:
- **Ensure Mayo VPN is connected** (required)
- Verify external service URLs in `local.runsettings`
- Check service account permissions
- Verify network connectivity to Mayo internal services

### Test data not found (404 errors)
**Cause**: Pre-configured test data doesn't exist

**Solutions**:
- Check test data IDs in `local.runsettings`
- IDs reference existing data in dev Orders Orchestration database
- Some tests create their own data and should work regardless
- Update IDs in `local.runsettings` if needed

### PDF/Label tests fail
**Cause**: QuestPDF licensing or native library issues

**Solutions**:
- Ensure QuestPDF license is configured
- Native libraries should work out-of-the-box on macOS/Windows
- Check for font-related errors