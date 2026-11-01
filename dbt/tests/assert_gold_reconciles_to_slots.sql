-- Gold must stay one row per slot. Any missing or extra slot_id fails the build.
with gold as (
    select slot_id from {{ ref('gold_provider_availability_daily') }}
),
slots as (
    select slot_id from {{ ref('silver_appointment_slots') }}
)
select slot_id from gold
except
select slot_id from slots
union all
select slot_id from slots
except
select slot_id from gold
