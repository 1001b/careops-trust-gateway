-- Defect fixture. Not an input to available_appointments.
-- A slot matches every network row for the same provider and payer, so slot_id repeats.
select
    s.slot_id,
    s.provider_id,
    s.slot_date,
    s.payer_network,
    n.status as network_status,
    n.effective_from,
    n.effective_to
from {{ ref('silver_appointment_slots') }} as s
inner join {{ ref('silver_provider_networks') }} as n
    on n.provider_id = s.provider_id
   and n.payer_network = s.payer_network
