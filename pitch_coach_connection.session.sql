create table users(
    id uuid not null primary key default gen_random_uuid(),
    email varchar(50) not null,
    name varchar(20) not null,
    created_at timestamp not null default now(),
    updated_at timestamp not null default now()
);

create table pitches(
    id uuid not null primary key default gen_random_uuid(),
    user_id uuid not null references users(id),
    title varchar(50) not null,
    time_limit_sec int not null,
    presentation_date date not null,
    -- takes 와 서로 참조하므로 FK 는 파일 맨 아래에서 alter 로 건다
    best_take_id uuid,
    created_at timestamp not null default now(),
    updated_at timestamp not null default now()
);

create table presentation_versions(
    id uuid not null primary key default gen_random_uuid(),
    pitch_id uuid not null references pitches(id),
    version int not null,
    file_url varchar(255) not null,
    created_at timestamp not null default now(),
    description text
);

create table script_versions(
    id uuid not null primary key default gen_random_uuid(),
    pitch_id uuid not null references pitches(id),
    version int not null,
    created_at timestamp not null default now()
);

create table script_slides(
    id uuid not null primary key default gen_random_uuid(),
    script_version_id uuid not null references script_versions(id),
    slide_number int not null,
    full_content text not null,
    highlights jsonb,
    keywords jsonb
);

create table takes(
    id uuid not null primary key default gen_random_uuid(),
    pitch_id uuid not null references pitches(id),
    take_number int not null,
    presentation_version_id uuid not null references presentation_versions(id),
    script_version_id uuid not null references script_versions(id),
    mode varchar(20) not null,
    script_mode varchar(20) not null,
    status varchar(20) not null,
    started_at timestamp,
    ended_at timestamp,
    duration_sec int,
    created_at timestamp not null default now(),
    event_logs jsonb
);

create table take_summaries(
    take_id uuid not null primary key references takes(id),
    average_wpm float not null,
    filler_count int not null,
    audience_gaze int not null,
    slide_gaze int not null,
    script_gaze int not null,
    script_dependency_count int not null,
    summary text not null,
    overall_confidence float not null,
    speak_accuracy float not null,
    volume numeric(5, 2) not null,
    total_intermission int not null,
    score int not null
);

create table calibrations(
    id uuid not null primary key default gen_random_uuid(),
    take_id uuid not null references takes(id),
    face_detected boolean not null default false,
    mic_detected boolean not null default false,
    base_volume numeric(5, 2) not null,
    gaze_confidence float not null,
    result jsonb,
    created_at timestamp not null default now()
);

create table live_feedbacks(
    id uuid not null primary key default gen_random_uuid(),
    take_id uuid not null references takes(id),
    type varchar(20) not null,
    message varchar(255) not null,
    triggered_at_ms int not null,
    confidence float not null
);

create table missions(
    id uuid not null primary key default gen_random_uuid(),
    source_take_id uuid not null references takes(id),
    slide_number int not null,
    description text not null,
    priority int not null,
    created_at timestamp not null default now(),
    complete boolean not null default false
);

-- pitches <-> takes 순환 참조. takes 생성 후에 건다.
alter table pitches
    add constraint pitches_best_take_id_fkey
    foreign key (best_take_id) references takes(id);

-- version 은 pitch 단위 순번이라 자동 증가가 아니라 애플리케이션이 채운다.
-- 같은 pitch 안에서만 중복을 막는다. pitch 가 다르면 version 이 같아도 된다.
alter table presentation_versions
    add constraint uq_presentation_versions_pitch_version unique (pitch_id, version);

alter table script_versions
    add constraint uq_script_versions_pitch_version unique (pitch_id, version);

alter table takes
    add constraint uq_takes_pitch_take_number unique (pitch_id, take_number);

alter table script_slides
    add constraint uq_script_slides_version_slide unique (script_version_id, slide_number);
