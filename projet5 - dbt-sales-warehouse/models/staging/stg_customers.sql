-- Clean customers: trimmed lowercase emails, one standard ISO country code
select
    id                                   as customer_id,
    trim(name)                           as customer_name,
    lower(trim(email))                   as email,
    case upper(trim(country))
        when 'FRANCE'  then 'FR'
        when 'MOROCCO' then 'MA'
        else upper(trim(country))
    end                                  as country_code,
    cast(signup_date as date)            as signup_date
from {{ ref('raw_customers') }}
