pub fn double(x: i32) -> i32 {
    x * 2
}

pub fn is_even(x: i32) -> bool {
    x % 2 == 0
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn doubles() {
        assert_eq!(double(3), 6);
    }

    #[test]
    fn even() {
        assert!(is_even(2));
    }
}
