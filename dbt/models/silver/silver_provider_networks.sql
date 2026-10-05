select
    provider_id,
    payer_network,
    try_cast(nullif(trim(cast(effective_from as varchar)), '') as date) as effective_from,
    try_cast(nullif(trim(cast(effective_to as varchar)), '') as date) as effective_to,
    lower(status) as status
from {{ ref('bronze_provider_networks') }}
