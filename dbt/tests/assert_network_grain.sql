-- Network grain is provider_id + payer_network + effective_from, not provider_id alone.
select
    provider_id,
    payer_network,
    effective_from
from {{ ref('silver_provider_networks') }}
group by 1, 2, 3
having count(*) > 1
