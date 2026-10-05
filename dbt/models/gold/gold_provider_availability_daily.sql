with slots as (
    select * from {{ ref('silver_appointment_slots') }}
),
providers as (
    select * from {{ ref('silver_providers') }}
),
networks as (
    select * from {{ ref('silver_provider_networks') }}
)
select
    s.slot_id,
    s.provider_id,
    p.provider_name,
    p.state,
    s.slot_date,
    s.payer_network,
    case
        when s.status = 'open'
         and s.slot_date >= p.active_from
         and (p.inactive_from is null or s.slot_date < p.inactive_from)
         and exists (
             select 1
             from networks n
             where n.provider_id = s.provider_id
               and n.payer_network = s.payer_network
               and n.status = 'eligible'
               and s.slot_date >= n.effective_from
               and (n.effective_to is null or s.slot_date <= n.effective_to)
         )
        then 1 else 0
    end as is_bookable
from slots s
join providers p using (provider_id)
