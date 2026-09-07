use bedrock_bus::{Bus, BusError, PhysicalMemoryClass};

pub(crate) const FAULT_TRANSLATION: i32 = 13;
pub(crate) const FAULT_ACCESS: i32 = 14;
pub(crate) const MIN_PAGE_BYTES: u64 = 1 << 14;

const IMPLEMENTATION_PABITS: u32 = 56;
const PHYSICAL_MASK: u64 = (1u64 << IMPLEMENTATION_PABITS) - 1;
const PTCR_ROOT_MASK: u64 = PHYSICAL_MASK & !0x3fff;
const PTCR_TT_MASK: u64 = 0b111 << 1;
const PTCR_DEFINED_MASK: u64 = PTCR_ROOT_MASK | PTCR_TT_MASK | 1;
const PTE_PRESENT: u64 = 1 << 0;
const PTE_TABLE: u64 = 1 << 1;
const PTE_AM_MASK: u64 = 0b111 << 2;
const PTE_TABLE_R: u64 = 1 << 2;
const PTE_TABLE_W: u64 = 1 << 3;
const PTE_TABLE_X: u64 = 1 << 4;
const PTE_USER: u64 = 1 << 5;
const PTE_ACCESSED: u64 = 1 << 7;
const PTE_DIRTY: u64 = 1 << 8;
const PTE_CP_MASK: u64 = 0b11 << 9;
const PTE_LEAF_PFN_MASK: u64 = PHYSICAL_MASK & !0x3fff;
const PTE_NEXT_TABLE_MASK: u64 = PHYSICAL_MASK & !0x0fff;
const PTE_UPPER_RESERVED_MASK: u64 = 0b11_1111 << 58;
const PTE_LEAF_RESERVED_MASK: u64 = PTE_UPPER_RESERVED_MASK | (0b111 << 11);
const PTE_TABLE_RESERVED_MASK: u64 = PTE_UPPER_RESERVED_MASK | (0b1111 << 8) | (1 << 6);
const PTE_UPPER_TABLE_ALIGNMENT_MASK: u64 = 0b11 << 12;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum TranslationAccess {
    Read,
    Write,
    Execute,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct TranslationFault {
    pub kind: i32,
    pub cause: i64,
    pub detail: String,
}

#[derive(Debug)]
pub(crate) enum TranslationError {
    Fault(TranslationFault),
    Bus(BusError),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct TranslationResult {
    pub linear_address: u64,
    pub address: u64,
    pub leaf_mask: Option<u64>,
    pub access_class: i32,
    pub physical_class: i32,
    pub cache_policy: i64,
}

impl TranslationResult {
    pub fn within_mapping(&self, bus: &impl Bus, linear: u64) -> Option<Self> {
        let mask = self.leaf_mask?;
        if linear & !mask != self.linear_address & !mask {
            return None;
        }
        let address = (self.address & !mask) | (linear & mask);
        Some(Self {
            linear_address: linear,
            address,
            physical_class: physical_class(bus.physical_memory_class(address)),
            ..*self
        })
    }
}

pub(crate) fn segment_linear(image: u64, effective: u64) -> Result<u64, TranslationFault> {
    let mantissa = (image >> 1) & 0x3f;
    if mantissa == 0 {
        return Ok(effective);
    }
    let base = u128::from((image >> 12) << 12);
    let span = (u128::from(mantissa) << ((image >> 7) & 0x1f)) * 4096;
    let point = u128::from(effective);
    let in_bounds = if image & 1 != 0 {
        base <= point && point < base + span
    } else {
        point < span
    };
    if !in_bounds {
        return Err(TranslationFault {
            kind: FAULT_TRANSLATION,
            cause: 4,
            detail: format!("address 0x{effective:016x} is outside the segment"),
        });
    }
    Ok(if image & 1 != 0 {
        effective
    } else {
        base.wrapping_add(point) as u64
    })
}

pub(crate) fn translate(
    bus: &mut impl Bus,
    linear: u64,
    ptcr: u64,
    access: TranslationAccess,
    user_domain: bool,
    supervisor: bool,
    on_walk_start: impl FnOnce(),
) -> Result<TranslationResult, TranslationError> {
    if ptcr & 1 == 0 {
        if linear >> IMPLEMENTATION_PABITS != 0 {
            return Err(access_fault(format!(
                "physical address 0x{linear:016x} exceeds PABITS"
            )));
        }
        return Ok(direct_result(bus, linear));
    }
    let (canonical_bits, shifts): (u8, &[u8]) = match (ptcr & PTCR_TT_MASK) >> 1 {
        0b010 => (45, &[34, 23, 14]),
        0b011 => (56, &[45, 34, 23, 14]),
        _ => return Err(page_fault(2, "invalid PTCR paging format")),
    };
    let root = ptcr & PTCR_ROOT_MASK;
    if ptcr & !PTCR_DEFINED_MASK != 0 || root & !PHYSICAL_MASK != 0 {
        return Err(page_fault(2, "invalid PTCR paging format"));
    }
    if !is_canonical(linear, canonical_bits) {
        return Err(page_fault(
            3,
            format!("non-canonical address 0x{linear:016x}"),
        ));
    }
    on_walk_start();
    'walk: loop {
        let mut table = root;
        let mut readable = true;
        let mut writable = true;
        let mut executable = true;
        let mut user = true;

        for (index, shift) in shifts.iter().copied().enumerate() {
            let level = (shifts.len() - index) as u8;
            let index_mask = if level == 1 { 0x1ff } else { 0x7ff };
            let entry_address = table
                .checked_add(((linear >> shift) & index_mask) * 8)
                .filter(|address| address & !PHYSICAL_MASK == 0)
                .ok_or_else(|| page_fault(2, "page-table entry address exceeds PABITS"))?;
            let mut entry = bus.read_u64(entry_address).map_err(TranslationError::Bus)?;
            if entry & PTE_PRESENT == 0 {
                return Err(page_fault(0, format!("level-{level} PTE is not present")));
            }
            let leaf = entry & PTE_TABLE == 0;
            let valid = if leaf {
                valid_leaf_entry(entry, level)
            } else {
                valid_table_entry(entry, level)
            };
            if !valid {
                return Err(page_fault(2, format!("invalid level-{level} PTE")));
            }

            let permissions = if leaf {
                leaf_permissions(entry)
            } else {
                table_permissions(entry)
            };
            readable &= permissions.0;
            writable &= permissions.1;
            executable &= permissions.2;
            user &= entry & PTE_USER != 0;
            let requires_user = user_domain || !supervisor;
            if (requires_user && !user) || (!requires_user && leaf && user) {
                return Err(page_fault(1, "page privilege violation"));
            }
            if access == TranslationAccess::Write && !writable {
                return Err(page_fault(1, "write to read-only page"));
            }
            if access == TranslationAccess::Execute && !executable {
                return Err(page_fault(1, "execution from non-executable page"));
            }
            if access == TranslationAccess::Read && !readable {
                return Err(page_fault(1, "read from non-readable page"));
            }

            let mut desired = entry | PTE_ACCESSED;
            if leaf && access == TranslationAccess::Write {
                desired |= PTE_DIRTY;
            }
            if desired != entry {
                match bus
                    .compare_exchange_u64(entry_address, entry, desired)
                    .map_err(TranslationError::Bus)?
                {
                    Ok(_) => entry = desired,
                    Err(_) => continue 'walk,
                }
            }

            if leaf {
                let address = (entry & PTE_LEAF_PFN_MASK) | (linear & leaf_offset_mask(level));
                return Ok(TranslationResult {
                    linear_address: linear,
                    address,
                    leaf_mask: Some(leaf_offset_mask(level)),
                    access_class: i32::from(((entry & PTE_AM_MASK) >> 2) >= 5),
                    physical_class: physical_class(bus.physical_memory_class(address)),
                    cache_policy: ((entry & PTE_CP_MASK) >> 9) as i64,
                });
            }
            table = entry & PTE_NEXT_TABLE_MASK;
        }
        return Err(page_fault(2, "page walk did not reach a leaf PTE"));
    }
}

fn direct_result(bus: &impl Bus, address: u64) -> TranslationResult {
    let class = physical_class(bus.physical_memory_class(address));
    TranslationResult {
        linear_address: address,
        address,
        leaf_mask: None,
        access_class: class,
        physical_class: class,
        cache_policy: 0,
    }
}

fn physical_class(class: PhysicalMemoryClass) -> i32 {
    match class {
        PhysicalMemoryClass::Normal => 0,
        PhysicalMemoryClass::Device => 1,
    }
}

fn page_fault(cause: i64, detail: impl Into<String>) -> TranslationError {
    TranslationError::Fault(TranslationFault {
        kind: FAULT_TRANSLATION,
        cause,
        detail: detail.into(),
    })
}

fn access_fault(detail: impl Into<String>) -> TranslationError {
    TranslationError::Fault(TranslationFault {
        kind: FAULT_ACCESS,
        cause: 0,
        detail: detail.into(),
    })
}

const fn valid_table_entry(entry: u64, level: u8) -> bool {
    level > 1
        && entry & (PTE_PRESENT | PTE_TABLE) == (PTE_PRESENT | PTE_TABLE)
        && entry & PTE_TABLE_RESERVED_MASK == 0
        && (level == 2 || entry & PTE_UPPER_TABLE_ALIGNMENT_MASK == 0)
        && entry & (PTE_TABLE_R | PTE_TABLE_W | PTE_TABLE_X) != 0
}

const fn valid_leaf_entry(entry: u64, level: u8) -> bool {
    entry & PTE_PRESENT != 0
        && entry & PTE_TABLE == 0
        && entry & PTE_LEAF_RESERVED_MASK == 0
        && entry & (leaf_offset_mask(level) & PTE_LEAF_PFN_MASK) == 0
}

const fn table_permissions(entry: u64) -> (bool, bool, bool) {
    (
        entry & PTE_TABLE_R != 0,
        entry & PTE_TABLE_W != 0,
        entry & PTE_TABLE_X != 0,
    )
}

const fn leaf_permissions(entry: u64) -> (bool, bool, bool) {
    match (entry & PTE_AM_MASK) >> 2 {
        0 | 5 => (true, false, false),
        1 | 6 => (false, true, false),
        2 => (false, false, true),
        3 | 7 => (true, true, false),
        4 => (true, false, true),
        _ => unreachable!(),
    }
}

const fn leaf_offset_mask(level: u8) -> u64 {
    match level {
        1 => MIN_PAGE_BYTES - 1,
        2 => (1u64 << 23) - 1,
        3 => (1u64 << 34) - 1,
        4 => (1u64 << 45) - 1,
        _ => 0,
    }
}

fn is_canonical(address: u64, bits: u8) -> bool {
    let shift = 64 - bits;
    (((address << shift) as i64) >> shift) as u64 == address
}

#[cfg(test)]
mod tests {
    use super::*;
    use bedrock_bus::{Bus, Ram};

    const TABLE: u64 = PTE_PRESENT | PTE_TABLE | PTE_TABLE_R | PTE_TABLE_W | PTE_TABLE_X;
    const LEAF_RW: u64 = PTE_PRESENT | (0b011 << 2);

    fn mapped_ram() -> Ram {
        let mut ram = Ram::new(0x20_000);
        ram.write_u64(0x4000, 0x8000 | TABLE).unwrap();
        ram.write_u64(0x8000, 0xc000 | TABLE).unwrap();
        ram.write_u64(0xc000, 0x1_0000 | TABLE).unwrap();
        ram.write_u64(0x1_0010, 0x1_4000 | LEAF_RW).unwrap();
        ram
    }

    #[test]
    fn la56_four_level_write_walk_sets_accessed_and_dirty() {
        let mut ram = mapped_ram();

        let result = translate(
            &mut ram,
            0x8000,
            0x4007,
            TranslationAccess::Write,
            false,
            true,
            || {},
        )
        .unwrap();

        assert_eq!(result.address, 0x1_4000);
        assert_eq!(result.access_class, 0);
        assert_eq!(
            ram.read_u64(0x1_0010).unwrap() & (PTE_ACCESSED | PTE_DIRTY),
            0x180
        );
    }

    #[test]
    fn noncanonical_address_reports_architectural_cause() {
        let mut ram = mapped_ram();

        let error = translate(
            &mut ram,
            0x0000_2000_0000_0000,
            0x4005,
            TranslationAccess::Read,
            false,
            true,
            || {},
        )
        .unwrap_err();

        assert!(matches!(
            error,
            TranslationError::Fault(TranslationFault {
                kind: FAULT_TRANSLATION,
                cause: 3,
                ..
            })
        ));
    }

    #[test]
    fn l2_pointer_preserves_four_kibibyte_aligned_l1_base() {
        let mut ram = Ram::new(0x20_000);
        ram.write_u64(0x4000, 0x8000 | TABLE).unwrap();
        ram.write_u64(0x8000, 0x9000 | TABLE).unwrap();
        ram.write_u64(0x9010, 0x1_4000 | LEAF_RW).unwrap();

        let result = translate(
            &mut ram,
            0x8000,
            0x4005,
            TranslationAccess::Read,
            false,
            true,
            || {},
        )
        .unwrap();

        assert_eq!(result.address, 0x1_4000);
    }

    #[test]
    fn upper_table_pointer_requires_sixteen_kibibyte_alignment() {
        let mut ram = Ram::new(0x20_000);
        ram.write_u64(0x4000, 0x9000 | TABLE).unwrap();

        let error =
            translate(&mut ram, 0, 0x4005, TranslationAccess::Read, false, true, || {}).unwrap_err();

        assert!(matches!(
            error,
            TranslationError::Fault(TranslationFault {
                kind: FAULT_TRANSLATION,
                cause: 2,
                ..
            })
        ));
    }
}
