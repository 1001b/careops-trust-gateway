// Phase 1: ACA + PostgreSQL (+ Key Vault shell for Phase 2). Secrets via secure deploy params.

param location string = resourceGroup().location

@description('Short name prefix for Azure resources')
param namePrefix string = 'careops-demo'

@description('PostgreSQL admin login')
param postgresAdminLogin string

@secure()
@description('PostgreSQL admin password')
param postgresAdminPassword string

@description('Container image (public GHCR recommended for Phase 1)')
param containerImage string

@description('GHCR username when the image is private (leave empty if public)')
param registryUsername string = ''

@secure()
@description('GHCR PAT with read:packages when the image is private (leave empty if public)')
param registryPassword string = ''

@description('Postgres Flexible Server SKU name (from az postgres flexible-server list-skus)')
param postgresSkuName string = 'Standard_D2s_v3'

@description('Postgres Flexible Server SKU tier: Burstable | GeneralPurpose | MemoryOptimized')
@allowed([
  'Burstable'
  'GeneralPurpose'
  'MemoryOptimized'
])
param postgresSkuTier string = 'GeneralPurpose'

@secure()
@description('Optional override; default is built from the deployed Postgres FQDN')
param databaseUrl string = ''

@secure()
@description('Optional Gemini API key')
param geminiApiKey string = ''

param minReplicas int = 0
param maxReplicas int = 2

var kvName = take('kv${uniqueString(resourceGroup().id, namePrefix, location)}', 24)
var pgName = take('pg${namePrefix}${uniqueString(resourceGroup().id, location)}', 60)
var caeName = 'cae-${namePrefix}-${location}'
var caName = 'ca-${namePrefix}-api-${location}'
var logName = take('log-${namePrefix}-${uniqueString(resourceGroup().id, location)}', 63)
var dbName = 'careops'

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2022-10-01' = {
  name: logName
  location: location
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
  }
}

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: kvName
  location: location
  properties: {
    tenantId: subscription().tenantId
    sku: {
      family: 'A'
      name: 'standard'
    }
    enableRbacAuthorization: true
    enableSoftDelete: true
  }
}

resource postgres 'Microsoft.DBforPostgreSQL/flexibleServers@2024-08-01' = {
  name: pgName
  location: location
  sku: {
    name: postgresSkuName
    tier: postgresSkuTier
  }
  properties: {
    version: '15'
    administratorLogin: postgresAdminLogin
    administratorLoginPassword: postgresAdminPassword
    storage: {
      storageSizeGB: 32
    }
    backup: {
      backupRetentionDays: 7
    }
    highAvailability: {
      mode: 'Disabled'
    }
    network: {
      publicNetworkAccess: 'Enabled'
    }
  }
}

resource postgresConfig 'Microsoft.DBforPostgreSQL/flexibleServers/configurations@2024-08-01' = {
  parent: postgres
  name: 'azure.extensions'
  properties: {
    value: 'VECTOR'
    source: 'user-override'
  }
}

resource postgresDb 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2024-08-01' = {
  parent: postgres
  name: dbName
  dependsOn: [
    postgresConfig
  ]
}

resource postgresFirewallAzure 'Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2024-08-01' = {
  parent: postgres
  name: 'AllowAzureServices'
  properties: {
    startIpAddress: '0.0.0.0'
    endIpAddress: '0.0.0.0'
  }
  dependsOn: [
    postgresDb
  ]
}

resource env 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: caeName
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalytics.properties.customerId
        sharedKey: logAnalytics.listKeys().primarySharedKey
      }
    }
  }
}

// Built after Postgres exists; uriComponent avoids breaking passwords with @/# etc.
var computedDatabaseUrl = 'postgresql://${postgresAdminLogin}:${uriComponent(postgresAdminPassword)}@${postgres.properties.fullyQualifiedDomainName}:5432/${dbName}?sslmode=require'
var effectiveDatabaseUrl = empty(databaseUrl) ? computedDatabaseUrl : databaseUrl

var appSecrets = concat(
  [
    {
      name: 'database-url'
      value: effectiveDatabaseUrl
    }
  ],
  empty(geminiApiKey) ? [] : [
    {
      name: 'gemini-api-key'
      value: geminiApiKey
    }
  ],
  empty(registryPassword) ? [] : [
    {
      name: 'ghcr-password'
      value: registryPassword
    }
  ]
)

var registryConfig = empty(registryPassword) ? [] : [
  {
    server: 'ghcr.io'
    username: registryUsername
    passwordSecretRef: 'ghcr-password'
  }
]

var baseEnv = [
  {
    name: 'DEMO_ENABLED'
    value: '1'
  }
  {
    name: 'CAREOPS_EMBEDDING_PROVIDER'
    value: 'local'
  }
  {
    name: 'CAREOPS_LLM_PROVIDER'
    value: 'auto'
  }
  {
    name: 'GEMINI_MODEL'
    value: 'gemini-3.8-flash'
  }
]

var secretEnv = concat(
  [
    {
      name: 'CAREOPS_DATABASE_URL'
      secretRef: 'database-url'
    }
  ],
  empty(geminiApiKey) ? [] : [
    {
      name: 'GEMINI_API_KEY'
      secretRef: 'gemini-api-key'
    }
  ]
)

resource containerApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: caName
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    managedEnvironmentId: env.id
    configuration: {
      ingress: {
        external: true
        targetPort: 8000
        transport: 'auto'
      }
      secrets: appSecrets
      registries: registryConfig
    }
    template: {
      containers: [
        {
          name: 'careops-api'
          image: containerImage
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: concat(baseEnv, secretEnv)
        }
      ]
      scale: {
        minReplicas: minReplicas
        maxReplicas: maxReplicas
      }
    }
  }
  dependsOn: [
    postgresFirewallAzure
  ]
}

output keyVaultName string = keyVault.name
output postgresFqdn string = postgres.properties.fullyQualifiedDomainName
output containerAppFqdn string = containerApp.properties.configuration.ingress.fqdn
output containerAppPrincipalId string = containerApp.identity.principalId
output postgresName string = postgres.name
