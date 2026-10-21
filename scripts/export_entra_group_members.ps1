[CmdletBinding()]
param(
    [Parameter()]
    [string]$OutputPath = "exports/opti-route-entraid-users.csv"
)

$ErrorActionPreference = "Stop"

# These are the Entra App Roles expected by Opti Route. A user must belong to
# exactly one of these groups, otherwise the application rejects the session.
$roleGroups = [ordered]@{
    "D-FR-APP-OPTIROUTE-ADMIN" = "admin"
    "D-FR-APP-OPTIROUTE-ATC"   = "atc"
    "D-FR-APP-OPTIROUTE-DR"    = "dr"
    "D-FR-APP-OPTIROUTE-RAG"   = "rag"
}

$requiredModules = @(
    "Microsoft.Graph.Authentication",
    "Microsoft.Graph.Groups"
)

foreach ($module in $requiredModules) {
    if (-not (Get-Module -ListAvailable -Name $module)) {
        throw (
            "The $module PowerShell module is required. Install it for the current user with: " +
            "Install-Module $module -Scope CurrentUser"
        )
    }
    Import-Module $module
}

# Read-only delegated permissions. An Entra administrator must grant consent
# if the tenant blocks consent for GroupMember.Read.All.
Connect-MgGraph -Scopes "GroupMember.Read.All", "User.ReadBasic.All" -NoWelcome

$rows = @()

foreach ($groupName in $roleGroups.Keys) {
    $escapedGroupName = $groupName.Replace("'", "''")
    $matches = @(
        Get-MgGroup -Filter "displayName eq '$escapedGroupName'" -All -Property "id,displayName"
    )

    if ($matches.Count -eq 0) {
        Write-Warning "Group not found in Microsoft Entra ID: $groupName"
        continue
    }

    if ($matches.Count -gt 1) {
        throw "More than one Entra group has the display name '$groupName'."
    }

    # Direct membership is deliberate: Entra enterprise app assignment doesn't
    # expand nested groups. Users are the only objects exported.
    $members = Get-MgGroupMemberAsUser `
        -GroupId $matches[0].Id `
        -All `
        -Property "id,displayName,mail,userPrincipalName"

    foreach ($member in $members) {
        $rows += [PSCustomObject]@{
            app_role          = $roleGroups[$groupName]
            entra_group       = $groupName
            object_id         = $member.Id
            display_name      = $member.DisplayName
            user_principal_name = $member.UserPrincipalName
            email             = $member.Mail
        }
    }
}

$duplicateAssignments = @(
    $rows |
        Group-Object object_id |
        Where-Object { $_.Count -gt 1 }
)

if ($duplicateAssignments.Count -gt 0) {
    $duplicateIds = $duplicateAssignments.Name -join ", "
    Write-Warning (
        "Users belong to more than one Opti Route role group: $duplicateIds. " +
        "Keep exactly one App Role per user before production."
    )
}

$outputDirectory = Split-Path -Parent $OutputPath
if ($outputDirectory) {
    New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null
}

$rows |
    Sort-Object app_role, display_name |
    Export-Csv -Path $OutputPath -NoTypeInformation -Encoding utf8

Write-Host "Exported $($rows.Count) Entra users to $OutputPath"
Disconnect-MgGraph | Out-Null
