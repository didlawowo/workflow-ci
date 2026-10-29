#![forbid(unsafe_code)]

pub fn add_one(value: u32) -> Option<u32> {
    value.checked_add(1)
}

#[cfg(test)]
mod tests {
    #[test]
    fn overflow_is_explicit() {
        assert_eq!(super::add_one(u32::MAX), None);
    }
}
