#ifndef BEDROCK_CORE_ABI_H
#define BEDROCK_CORE_ABI_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define BEDROCK_CORE_MAX_INSTRUCTION_BYTES 18u
#define BEDROCK_CORE_REGISTER_COUNT 16u
#define BEDROCK_CORE_FLOATING_REGISTER_COUNT 16u
#define BEDROCK_CORE_SEGMENT_COUNT 9u
#define BEDROCK_CORE_VECTOR_REGISTER_COUNT 32u
#define BEDROCK_CORE_PREDICATE_REGISTER_COUNT 16u
#define BEDROCK_CORE_MAX_VECTOR_LENGTH_BYTES 16u
#define BEDROCK_CORE_MAX_PREDICATE_LENGTH_BYTES 2u

typedef struct bedrock_core bedrock_core;

typedef enum bedrock_core_status {
  BEDROCK_CORE_OK = 0,
  BEDROCK_CORE_INVALID_INSTRUCTION = 1,
  BEDROCK_CORE_NEEDS_ENVIRONMENT = 2,
  BEDROCK_CORE_FAULT = 3,
  BEDROCK_CORE_BAD_ARGUMENT = 4,
  BEDROCK_CORE_OUT_OF_MEMORY = 5,
  BEDROCK_CORE_BAD_STATE = 6,
  BEDROCK_CORE_VECTOR_LANE = 7,
  BEDROCK_CORE_DEBUG_STOP = 8
} bedrock_core_status;

typedef struct bedrock_core_fault {
  int32_t kind;
  int32_t operation;
  uint64_t error_code;
  uint8_t bus_error;
  uint8_t address_valid;
  uint64_t effective_address;
  uint64_t linear_address;
  int64_t width;
} bedrock_core_fault;

typedef struct bedrock_core_control_state {
  uint64_t base_ptcr;
  uint64_t base_ascr;
  uint64_t base_ecr;
  uint64_t base_upc;
  uint64_t base_usp;
  uint64_t base_ucs;
  uint64_t base_uds;
  uint64_t base_uss;
  uint64_t base_uctl;
  uint64_t base_uinfo;
  uint64_t base_epc;
  uint64_t base_ecs;
  uint64_t base_eds;
  uint64_t base_sss;
  uint64_t base_ssp;
  uint64_t base_iss;
  uint64_t base_isp;
  uint64_t base_fss;
  uint64_t base_fsp;
  uint64_t base_dss;
  uint64_t base_dsp;
  uint64_t base_bootpc;
  uint64_t base_bootcfg;
  uint64_t base_pmc;
} bedrock_core_control_state;

typedef struct bedrock_core_state {
  uint64_t registers[BEDROCK_CORE_REGISTER_COUNT];
  uint64_t floating_registers[BEDROCK_CORE_FLOATING_REGISTER_COUNT];
  uint8_t vector_registers[BEDROCK_CORE_VECTOR_REGISTER_COUNT]
                          [BEDROCK_CORE_MAX_VECTOR_LENGTH_BYTES];
  uint8_t predicate_registers[BEDROCK_CORE_PREDICATE_REGISTER_COUNT]
                             [BEDROCK_CORE_MAX_PREDICATE_LENGTH_BYTES];
  uint64_t sp;
  uint64_t pc;
  uint64_t lpc;
  uint64_t lpa;
  uint64_t lkl;
  uint64_t lkh;
  uint64_t flags;
  uint64_t status;
  uint64_t segments[BEDROCK_CORE_SEGMENT_COUNT];
  bedrock_core_control_state controls;
  uint64_t interrupt_max_id;
  uint64_t interrupt_threshold;
  uint64_t interrupt_selector;
  uint64_t time_value;
  uint64_t time_ticks_per_second;
  uint64_t timer_deadline;
  uint64_t timer_interrupt_identity;
  uint8_t timer_armed;
  uint64_t fstatus;
  uint64_t fflags;
  uint64_t vstatus;
  uint8_t current_dfa;
  uint8_t supervisor;
  uint8_t halted;
  int32_t run_state;
  uint8_t fp_enabled;
  uint8_t fp16_convert_enabled;
  uint8_t fptrans_enabled;
  uint8_t vector_enabled;
  int64_t cache_maintenance_granule;
  uint8_t fp_state_modified;
  uint8_t vector_state_modified;
  int64_t max_vector_length_bytes;
  uint64_t machine_check_error_code;
  uint64_t machine_check_event_aux;
  uint64_t machine_check_fault_ea;
  uint64_t machine_check_fault_linear;
  uint64_t machine_check_payload;
  uint8_t machine_check_pending;
  uint64_t nmi_latched_source;
  uint8_t nmi_relatched;
  uint64_t nmi_relatched_source;
  uint8_t repeat_active;
  uint64_t repeat_body_start;
  uint64_t repeat_condition;
  uint64_t repeat_counter;
  uint64_t repeat_prefix_start;
  uint64_t repeat_remaining;
  uint8_t repeat_fixed_body[BEDROCK_CORE_MAX_INSTRUCTION_BYTES];
  size_t repeat_fixed_body_length;
} bedrock_core_state;

typedef struct bedrock_core_request {
  int32_t kind;
  int32_t operation;
  int32_t form_id;
  int32_t access;
  int32_t role;
  int32_t domain;
  int64_t segment;
  uint64_t segment_image;
  int64_t width;
  int64_t ordinal;
  int64_t range_length;
  uint8_t range_wrap;
  uint8_t range_end_at_modulus;
  uint64_t effective_address;
  uint64_t linear_address;
  uint64_t value;
  uint64_t desired;
  uint64_t expected;
  uint64_t range_start;
  uint64_t range_end;
  uint8_t address_translation;
  uint8_t debug_validation;
  uint8_t debug_validated;
  uint64_t physical_address;
  int32_t read_completion;
  int32_t memory_cache_hint;
  size_t memory_range_count;
  size_t validation_range_count;
  uint8_t commit_point;
  int64_t memory_order;
  int64_t cache_policy;
  uint8_t suppress_fault;
  uint64_t selector;
  uint64_t auxiliary;
  int64_t body_length;
  size_t payload_length;
} bedrock_core_request;

typedef struct bedrock_core_memory_range {
  uint64_t effective_address;
  uint64_t linear_address;
  int64_t width;
  int64_t buffer_offset;
} bedrock_core_memory_range;

typedef struct bedrock_core_validation_range {
  uint64_t effective_address;
  uint64_t linear_address;
  int64_t width;
} bedrock_core_validation_range;

typedef struct bedrock_core_response {
  int32_t kind;
  uint8_t success;
  int32_t fault_kind;
  int64_t fault_cause;
  uint8_t fault_range_present;
  bedrock_core_validation_range fault_range;
  const char *detail;
  uint64_t value;
  uint64_t secondary_value;
  uint8_t flags;
  uint8_t write_flags;
  uint8_t generated_fflags;
  uint8_t bounds_passed;
  int64_t cache_policy;
  int32_t access_class;
  int32_t physical_class;
  uint8_t atomic_store_happened;
  const uint8_t *body_bytes;
  size_t body_length;
  uint8_t known;
  uint8_t present;
} bedrock_core_response;

/* The generated Sail runtime owns process-global state. Serialize all calls. */
size_t bedrock_core_state_size(void);
bedrock_core *bedrock_core_create(void);
bedrock_core *bedrock_core_clone(const bedrock_core *source);
void bedrock_core_destroy(bedrock_core *core);
bedrock_core_status bedrock_core_reset(bedrock_core *core);

bedrock_core_status bedrock_core_get_pc(const bedrock_core *core, uint64_t *value);
bedrock_core_status bedrock_core_set_pc(bedrock_core *core, uint64_t value);
bedrock_core_status bedrock_core_get_sp(const bedrock_core *core, uint64_t *value);
bedrock_core_status bedrock_core_set_sp(bedrock_core *core, uint64_t value);
bedrock_core_status bedrock_core_get_register(
    const bedrock_core *core, uint32_t index, uint64_t *value);
bedrock_core_status bedrock_core_set_register(
    bedrock_core *core, uint32_t index, uint64_t value);
bedrock_core_status bedrock_core_get_status(
    const bedrock_core *core, uint64_t *value);
bedrock_core_status bedrock_core_get_control(
    const bedrock_core *core, uint32_t selector, uint64_t *value);
bedrock_core_status bedrock_core_post_interrupt(
    bedrock_core *core, uint32_t identity);
bedrock_core_status bedrock_core_advance_time(
    bedrock_core *core, uint64_t ticks);
bedrock_core_status bedrock_core_is_supervisor(
    const bedrock_core *core, uint8_t *value);
bedrock_core_status bedrock_core_get_state(
    const bedrock_core *core, bedrock_core_state *state);
bedrock_core_status bedrock_core_set_state(
    bedrock_core *core, const bedrock_core_state *state);

bedrock_core_status bedrock_core_execute(
    bedrock_core *core, const uint8_t *bytes, size_t length);
bedrock_core_status bedrock_core_last_fault(
    const bedrock_core *core, bedrock_core_fault *fault);
bedrock_core_status bedrock_core_last_request(
    const bedrock_core *core, bedrock_core_request *request);
bedrock_core_status bedrock_core_request_payload(
    const bedrock_core *core, uint8_t *buffer, size_t capacity, size_t *length);
bedrock_core_status bedrock_core_request_memory_ranges(
    const bedrock_core *core, bedrock_core_memory_range *buffer,
    size_t capacity, size_t *count);
bedrock_core_status bedrock_core_request_validation_ranges(
    const bedrock_core *core, bedrock_core_validation_range *buffer,
    size_t capacity, size_t *count);
bedrock_core_status bedrock_core_cancel(bedrock_core *core);
bedrock_core_status bedrock_core_resume(
    bedrock_core *core, const bedrock_core_response *response);

#ifdef __cplusplus
}
#endif

#endif
