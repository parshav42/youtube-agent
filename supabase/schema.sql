create table if not exists public.video_jobs (
    id text primary key,
    idea text not null,
    status text not null,
    stage text not null,
    message text not null,
    error text,
    result jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

alter table public.video_jobs enable row level security;
revoke all on public.video_jobs from anon, authenticated;
grant all on public.video_jobs to service_role;

create or replace function public.update_video_job_timestamp()
returns trigger
language plpgsql
as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

drop trigger if exists video_jobs_updated_at on public.video_jobs;
create trigger video_jobs_updated_at
before update on public.video_jobs
for each row
execute function public.update_video_job_timestamp();

insert into storage.buckets (id, name, public)
values ('video-assets', 'video-assets', false)
on conflict (id) do nothing;
