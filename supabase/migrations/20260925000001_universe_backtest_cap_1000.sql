-- migration: 20260925000001_universe_backtest_cap_1000
-- date: 2026-09-25
-- purpose: raise the universe backtest ticker cap from 500 to 1000 so the
--   954-ticker default universe can be backtested. Function bodies are otherwise
--   identical to 20260831000002; create or replace keeps existing grants.

create or replace function universe_hit_rates(
  p_tickers text[], p_horizon_days int, p_bucket text default 'strength'
) returns table (bucket_key text, hits bigint, total bigint, hit_rate double precision)
language plpgsql stable security definer set search_path = public as $$
declare
begin
  if coalesce(array_length(p_tickers, 1), 0) > 1000 then
    raise exception 'universe_hit_rates: too many tickers (max 1000)';
  end if;

  return query
  select
    case p_bucket when 'category' then category
                  when 'ticker'   then ticker
                  when 'detector' then detector
                  else strength end as bucket_key,
    count(*) filter (where hit) as hits,
    count(*)                    as total,
    count(*) filter (where hit)::float8 / nullif(count(*), 0) as hit_rate
  from detector_outcomes
  where ticker = any(p_tickers)
    and horizon_days = p_horizon_days
    and hit is not null
  group by 1
  order by 1;
end;
$$;

create or replace function universe_backtest_meta(
  p_tickers text[], p_horizon_days int
) returns table (tickers_scored bigint, hits_total bigint, signals_total bigint, baseline_up_rate double precision)
language plpgsql stable security definer set search_path = public as $$
declare
begin
  if coalesce(array_length(p_tickers, 1), 0) > 1000 then
    raise exception 'universe_backtest_meta: too many tickers (max 1000)';
  end if;

  -- hits_total / signals_total are over directional rows only (hit is not
  -- null); baseline_up_rate is the *unconditional* up-rate over every bar in
  -- scope (NEUTRAL rows included) — the honest "what if you'd just held"
  -- comparison the design doc §5 item #18 asks for.
  return query
  select
    (select count(distinct o.ticker)
       from detector_outcomes o
      where o.ticker = any(p_tickers)
        and o.horizon_days = p_horizon_days
        and o.hit is not null)                                            as tickers_scored,
    (select count(*) filter (where o.hit)
       from detector_outcomes o
      where o.ticker = any(p_tickers)
        and o.horizon_days = p_horizon_days
        and o.hit is not null)                                            as hits_total,
    (select count(*)
       from detector_outcomes o
      where o.ticker = any(p_tickers)
        and o.horizon_days = p_horizon_days
        and o.hit is not null)                                            as signals_total,
    (select count(*) filter (where o.pct_return > 0)::float8
              / nullif(count(*), 0)
       from detector_outcomes o
      where o.ticker = any(p_tickers)
        and o.horizon_days = p_horizon_days)                              as baseline_up_rate;
end;
$$;

revoke all on function universe_hit_rates(text[], int, text) from public;
grant execute on function universe_hit_rates(text[], int, text) to anon, authenticated;

revoke all on function universe_backtest_meta(text[], int) from public;
grant execute on function universe_backtest_meta(text[], int) to anon, authenticated;
