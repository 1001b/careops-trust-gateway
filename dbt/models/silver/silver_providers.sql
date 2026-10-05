select
    provider_id,
    trim(provider_name) as provider_name,
    upper(state) as state,
    try_cast(nullif(trim(cast(active_from as varchar)), '') as date) as active_from,
    try_cast(nullif(trim(cast(inactive_from as varchar)), '') as date) as inactive_from
from {{ ref('bronze_providers') }}
